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
from core.calendario import restar_habiles
from core.postgres import get_pool
from core.tz import ahora_ar


def rango_default(hasta: date | None = None) -> tuple[date, date]:
    """El rango por defecto: el día HÁBIL anterior a `hasta` y `hasta`.

    **El día hábil anterior y hoy**, NO "ayer y hoy" de calendario. Los bancos
    no operan sábados, domingos ni feriados: un rango que cae en un día no hábil
    muestra la pantalla vacía, y una pantalla vacía acá no se lee como "el rango
    está mal elegido" sino como "no hubo movimientos" — que es una conclusión
    distinta y falsa.

    Pasó el 2026-08-18 (martes): el lunes 17 fue feriado, el default pidió
    17..18 y la vista no mostró un solo movimiento. El "ayer" que correspondía
    era el viernes 14.

    Tiene que coincidir con la ventana que ingesta `jobs/interbanking_sync`
    (`ventana()`): si la vista pidiera un día que el job no trae, la pantalla
    mostraría un hueco que no existe en el banco. Los dos usan la MISMA
    primitiva, `core.calendario.restar_habiles`.

    Sin `hasta`, el "hoy" sale de la hora ARGENTINA y no del reloj del proceso:
    el Droplet corre en UTC y a partir de las 21 ART ya está en el día
    siguiente. Con `hasta`, sirve igual para un día pasado que el back office
    elija a mano: el par que devuelve sigue siendo (hábil anterior, ese día).
    """
    hasta = hasta or ahora_ar().date()
    return restar_habiles(hasta, 1), hasta


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
        # COD OP BCO y SUCURSAL: se venían guardando desde la primera corrida y
        # no se publicaban. El back office los pidió el 2026-08-18 y no costaron
        # ni una llamada nueva ni un backfill — el dato ya estaba en la base.
        "codigo_banco": r.get("codigo_operacion_banco"),
        "sucursal": (str(r.get("sucursal")).strip() if r.get("sucursal") is not None else None),
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
                      descripcion_ib, codigo_operacion_ib, codigo_operacion_banco,
                      sucursal, numero_extracto,
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


def consolidado(email: str, desde: date, hasta: date) -> dict:
    """UNA fila por cuenta, agrupadas por banco, con saldo al inicio y al cierre.

    El saldo al inicio es la APERTURA del primer día con extracto dentro del
    rango, y el de cierre el CIERRE del último. No se calcula: los dos los
    informa el banco.

    ⚠️ Lista **todas** las cuentas activas. Una cuenta sin extracto en el rango NO
    va con cero — cero sería inventar un número.

    De dónde sale el saldo, por orden y siempre declarado en `fuente`:

    1. **`extracto`** — apertura del primer día con extracto y cierre del último.
       Es lo mejor que hay: los dos los informa el banco y vienen con el detalle
       de movimientos que los explica.
    2. **`saldo`** — `bancos.saldos`. Entra cuando el extracto no existe, que es
       el caso de la cuenta QUIETA: el extracto solo devuelve los días CON
       movimientos, así que con la ventana corta del back office (último día
       hábil + hoy) cualquier cuenta que no se movió no tenía ninguna fila.
    3. **`null`** — no sabemos. Se cuenta en `sin_datos` y la vista lo canta.

    Las dos fuentes NO se suman ni se promedian: por cuenta gana una sola, y la
    respuesta dice cuál. Si el banco informa un cierre de extracto distinto del
    saldo del día, eso es un HALLAZGO de conciliación (`discrepancia`), no un
    número a elegir por nosotros.

    Los totales van **por moneda y nunca mezclados**: sumar pesos con dólares no
    significa nada (mismo criterio que el control de saldos de comitentes).
    """
    filas = _q(
        """WITH rango AS (
               SELECT cuenta_id, min(fecha) AS f_ini, max(fecha) AS f_fin,
                      count(*) AS dias
                 FROM bancos.extracto_dia
                WHERE fecha BETWEEN %s AND %s
                GROUP BY cuenta_id
           ),
           -- El saldo del banco. Se toma el ÚLTIMO día del rango que tenga fila,
           -- no un promedio ni una suma: es un stock, no un flujo.
           saldo AS (
               SELECT DISTINCT ON (cuenta_id)
                      cuenta_id, fecha AS s_fecha,
                      coalesce(saldo_operativo, saldo_dia) AS s_saldo,
                      proyectado_24hs, proyectado_48hs
                 FROM bancos.saldos
                WHERE fecha BETWEEN %s AND %s
                  AND coalesce(saldo_operativo, saldo_dia) IS NOT NULL
                ORDER BY cuenta_id, fecha DESC
           )
           SELECT c.id, c.bank_number, c.bank_name, c.account_number,
                  c.account_type, c.currency, c.account_label, c.activa,
                  ei.saldo_apertura AS saldo_inicio,
                  ef.saldo_cierre   AS saldo_cierre,
                  r.dias, r.f_ini, r.f_fin,
                  s.s_saldo, s.s_fecha, s.proyectado_24hs, s.proyectado_48hs
             FROM bancos.cuentas c
             LEFT JOIN rango r  ON r.cuenta_id = c.id
             LEFT JOIN saldo s  ON s.cuenta_id = c.id
             LEFT JOIN bancos.extracto_dia ei
                    ON ei.cuenta_id = c.id AND ei.fecha = r.f_ini
             LEFT JOIN bancos.extracto_dia ef
                    ON ef.cuenta_id = c.id AND ef.fecha = r.f_fin
            WHERE c.activa
            ORDER BY c.bank_name, c.currency, c.account_type, c.account_number""",
        (desde, hasta, desde, hasta),
    )

    bancos: list[dict] = []
    por_banco: dict[str, dict] = {}
    totales: dict[str, dict] = {}
    sin_datos = 0

    for r in filas:
        pub = _cuenta_publica(r)
        ini, fin = _f(r.get("saldo_inicio")), _f(r.get("saldo_cierre"))
        del_banco = _f(r.get("s_saldo"))

        # Quién manda: el extracto si lo hay, si no el saldo. Nunca los dos.
        if fin is not None:
            fuente = "extracto"
        elif del_banco is not None:
            fuente, fin = "saldo", del_banco
        else:
            fuente = None
            sin_datos += 1

        # Si el banco informa las dos cosas y no coinciden, es un hallazgo de
        # conciliación — se publica, no se elige una y se tapa la otra.
        discrepancia = None
        if r.get("saldo_cierre") is not None and del_banco is not None:
            d = round(_f(r["saldo_cierre"]) - del_banco, 2)
            if abs(d) >= 0.01:
                discrepancia = d

        cuenta = {
            **pub,
            "saldo_inicio": ini,
            "saldo_cierre": fin,
            "fuente": fuente,
            "saldo_banco": del_banco,
            "saldo_banco_fecha": r["s_fecha"].isoformat() if r.get("s_fecha") else None,
            "discrepancia": discrepancia,
            "proyectado_24hs": _f(r.get("proyectado_24hs")),
            "proyectado_48hs": _f(r.get("proyectado_48hs")),
            # La variación solo existe si están los dos extremos Y el cierre sale
            # del extracto: restar una apertura de extracto contra un saldo de
            # otra fuente mezclaría dos cosas que el banco informa por separado.
            "variacion": (round(fin - ini, 2)
                          if fuente == "extracto" and ini is not None and fin is not None
                          else None),
            "dias_con_dato": r.get("dias") or 0,
            "desde_real": r["f_ini"].isoformat() if r.get("f_ini") else None,
            "hasta_real": r["f_fin"].isoformat() if r.get("f_fin") else None,
        }

        clave = f"{r.get('bank_number')}|{(r.get('bank_name') or '').strip()}"
        grupo = por_banco.get(clave)
        if grupo is None:
            grupo = {
                "banco": r.get("bank_number"),
                "banco_nombre": (r.get("bank_name") or "").strip(),
                "cuentas": [],
                "totales": {},
            }
            por_banco[clave] = grupo
            bancos.append(grupo)
        grupo["cuentas"].append(cuenta)

        # Totales por moneda, del banco y globales. Solo suman las cuentas con
        # dato — la que no tiene ni extracto ni saldo no aporta ni resta.
        # Desde que existe el fallback a `bancos.saldos`, el CIERRE incluye a las
        # cuentas quietas (antes quedaban fuera del total y el total mentía por
        # abajo); el INICIO sigue saliendo solo del extracto, así que una cuenta
        # servida por saldo suma al cierre y no al inicio. Es correcto: de esa
        # cuenta sabemos cuánto HAY, no cuánto había al arrancar el rango.
        for destino in (grupo["totales"], totales):
            acc = destino.setdefault(
                pub["moneda"], {"inicio": 0.0, "cierre": 0.0, "cuentas": 0}
            )
            if ini is not None or fin is not None:
                acc["inicio"] += ini or 0
                acc["cierre"] += fin or 0
                acc["cuentas"] += 1

    for destino in [*(g["totales"] for g in bancos), totales]:
        for acc in destino.values():
            acc["inicio"] = round(acc["inicio"], 2)
            acc["cierre"] = round(acc["cierre"], 2)
            acc["variacion"] = round(acc["cierre"] - acc["inicio"], 2)

    _auditar(email, None, desde, hasta, len(filas))

    return {
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "bancos": bancos,
        "totales": totales,
        "cuentas": len(filas),
        "sin_datos": sin_datos,
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
