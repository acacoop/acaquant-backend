"""api/services/bancos.py — lectura de `bancos.*` para la tab INTERBANKING.

Módulo PURO (sin FastAPI). Lee SOLO de Postgres: **nunca** le pega a
Interbanking. El que habla con Interbanking es `jobs/interbanking_sync`, porque
el límite de 100 llamadas/minuto es del ABONADO y no del proceso — una pantalla
que consultara en vivo podría agotar la cuota y romper el job.

## Regla de exposición (SEGURIDAD)

Son los datos bancarios de la casa. La API devuelve **campos explícitos**, nunca
la fila entera ni el `raw` jsonb. Concretamente, esto NO sale nunca:

- `account_cbu` y `account_cuit` de nuestras cuentas.
- `account_number` completo — se publica solo la terminación (`referencia`).
- el `raw` de cualquier tabla.
- el CUIT de la contraparte, que va enmascarado (son datos personales de
  terceros y vienen en ~3% de los movimientos).

Está congelado por `tests/unit/test_interbanking_seguridad.py`: si alguien
agrega uno de esos campos a la proyección pública, el test falla.
"""
from __future__ import annotations

from datetime import date

from api.services._sql import _f, _q
from core.postgres import get_pool


def _exec(sql: str, params: tuple) -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        conn.commit()


# --------------------------------------------------------------------------- #
# Proyecciones públicas
# --------------------------------------------------------------------------- #
def _cuenta_publica(r: dict) -> dict:
    """Lo que la vista puede ver de una cuenta. Sin CBU, sin CUIT, sin número."""
    nro = str(r.get("account_number") or "")
    return {
        "id": r["id"],
        "banco": r.get("bank_number"),
        "banco_nombre": (r.get("bank_name") or "").strip(),
        "tipo": r.get("account_type"),
        "moneda": r.get("currency"),
        "etiqueta": (r.get("account_label") or "").strip(),
        "referencia": f"…{nro[-4:]}" if len(nro) >= 4 else "",
        "activa": r.get("activa"),
    }


def _cuit_enmascarado(v: str | None) -> str | None:
    """CUIT de terceros: se muestra lo justo para reconocerlo, no para copiarlo."""
    s = "".join(ch for ch in str(v or "") if ch.isdigit())
    return f"{s[:2]}-…-{s[-1:]}" if len(s) >= 8 else None


def _movimiento_publico(r: dict) -> dict:
    return {
        "fecha": r["fecha"].isoformat() if r.get("fecha") else None,
        "hora": r["fecha_proceso"].strftime("%H:%M:%S") if r.get("fecha_proceso") else None,
        "importe": _f(r.get("importe")),
        "tipo": r.get("tipo"),
        "descripcion": (r.get("descripcion_banco") or r.get("descripcion_ib") or "").strip(),
        "concepto": (r.get("descripcion_ib") or "").strip(),
        "codigo": r.get("codigo_operacion_ib"),
        "extracto": r.get("numero_extracto"),
        "correlativo": r.get("correlativo"),
        "comprobante": r.get("comprobante"),
        "contraparte": (r.get("denominacion_contraparte") or "").strip() or None,
        "contraparte_cuit": _cuit_enmascarado(r.get("cuit_contraparte")),
    }


def _dia_publico(r: dict) -> dict:
    return {
        "fecha": r["fecha"].isoformat() if r.get("fecha") else None,
        "saldo_apertura": _f(r.get("saldo_apertura")),
        "saldo_cierre": _f(r.get("saldo_cierre")),
        "creditos": _f(r.get("total_creditos")),
        "debitos": _f(r.get("total_debitos")),
        "movimientos_banco": r.get("total_movimientos"),
        "movimientos_base": r.get("movimientos_base"),
        "cierra": r.get("cierra"),
        "diferencia": _f(r.get("diferencia")),
        "sincronizado_at": (r["sincronizado_at"].isoformat()
                            if r.get("sincronizado_at") else None),
    }


# --------------------------------------------------------------------------- #
# Lecturas
# --------------------------------------------------------------------------- #
def listar_cuentas() -> list[dict]:
    filas = _q(
        """SELECT id, bank_number, bank_name, account_number, account_type,
                  currency, account_label, activa
             FROM bancos.cuentas
            WHERE activa
            ORDER BY bank_name, currency, account_type, account_number"""
    )
    return [_cuenta_publica(r) for r in filas]


def vista(email: str, cuenta_id: int | None, desde: date, hasta: date) -> dict:
    """TODO lo que muestra la tab, en UN request.

    Mismo criterio que `senebis.vista` y `aca.vista`: una tab que pollea cada
    sub-panel por separado multiplica los viajes a la base por usuario.
    """
    cuentas = listar_cuentas()
    if cuenta_id is None and cuentas:
        cuenta_id = cuentas[0]["id"]

    dias: list[dict] = []
    movimientos: list[dict] = []

    if cuenta_id is not None:
        dias = [_dia_publico(r) for r in _q(
            """SELECT e.fecha, e.saldo_apertura, e.saldo_cierre, e.total_creditos,
                      e.total_debitos, e.total_movimientos, e.cierra, e.diferencia,
                      e.sincronizado_at,
                      (SELECT count(*) FROM bancos.movimientos m
                        WHERE m.cuenta_id = e.cuenta_id AND m.fecha = e.fecha)
                        AS movimientos_base
                 FROM bancos.extracto_dia e
                WHERE e.cuenta_id = %s AND e.fecha BETWEEN %s AND %s
                ORDER BY e.fecha DESC""",
            (cuenta_id, desde, hasta))]

        movimientos = [_movimiento_publico(r) for r in _q(
            """SELECT fecha, fecha_proceso, importe, tipo, descripcion_banco,
                      descripcion_ib, codigo_operacion_ib, numero_extracto,
                      correlativo, comprobante, cuit_contraparte,
                      denominacion_contraparte
                 FROM bancos.movimientos
                WHERE cuenta_id = %s AND fecha BETWEEN %s AND %s
                ORDER BY fecha DESC, numero_extracto DESC, correlativo""",
            (cuenta_id, desde, hasta))]

    creditos = sum(m["importe"] or 0 for m in movimientos if m["tipo"] == "C")
    debitos = sum(m["importe"] or 0 for m in movimientos if m["tipo"] == "D")

    resumen = {
        "dias": len(dias),
        "movimientos": len(movimientos),
        "creditos": round(creditos, 2),
        "debitos": round(debitos, 2),
        "neto": round(creditos - debitos, 2),
        # Las dos alertas de conciliación. Se calculan acá, no en el front.
        "dias_que_no_cierran": [d["fecha"] for d in dias if d["cierra"] is False],
        "dias_incompletos": [d["fecha"] for d in dias
                             if d["movimientos_banco"] is not None
                             and d["movimientos_banco"] != d["movimientos_base"]],
        "saldo_final": next((d["saldo_cierre"] for d in dias), None),
    }

    _auditar(email, cuenta_id, desde, hasta, len(movimientos))

    return {
        "cuentas": cuentas,
        "cuenta_id": cuenta_id,
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "dias": dias,
        "movimientos": movimientos,
        "resumen": resumen,
        "sync": ultima_sync(),
    }


def ultima_sync() -> dict | None:
    """Última corrida del job. Es lo que contesta «¿este dato de cuándo es?»."""
    filas = _q(
        """SELECT max(corrida_at) AS corrida_at,
                  count(*) FILTER (WHERE NOT ok) AS con_error,
                  count(*) AS cuentas
             FROM bancos.sync_log
            WHERE corrida_at >= (SELECT max(corrida_at) - interval '10 minutes'
                                   FROM bancos.sync_log)"""
    )
    if not filas or not filas[0].get("corrida_at"):
        return None
    r = filas[0]
    return {
        "corrida_at": r["corrida_at"].isoformat(),
        "cuentas": r["cuentas"],
        "con_error": r["con_error"],
    }


def _auditar(email: str, cuenta_id: int | None, desde: date, hasta: date, filas: int) -> None:
    """Quién miró qué banco y cuándo. Nunca rompe la lectura."""
    try:
        _exec(
            """INSERT INTO bancos.audit_lecturas (email, cuenta_id, fecha_desde,
                                                  fecha_hasta, filas)
               VALUES (%s,%s,%s,%s,%s)""",
            (email, cuenta_id, desde, hasta, filas),
        )
    except Exception:  # la auditoría no puede tumbar la vista
        pass
