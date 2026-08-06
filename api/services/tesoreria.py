"""api/services/tesoreria.py — Back Office → Tesorería (ingresos/egresos del día).

Fuente: Aunesa `GET cuentas/consultaMovDocsSolicitados` ("Movimientos y Documentos
Solicitados") — movimientos BANCARIOS de dinero (transferencias, transferencia MEP,
e-cheq). La DIRECCIÓN la da el campo `solicitud` (Depósito = ingreso / Extracción =
egreso), NO el signo del `monto` (siempre positivo). Plata efectiva = `estado`
'Procesado' (default). Modelo verificado por discovery 2026-07-03.

Puro (sin FastAPI): lo llama `api/routers/back_office.py`. Se sirve LIVE contra Aunesa
sin persistir — volumen chico (~cientos de mov/día). El histórico de saldos (si se
necesita) se congelará con un job aparte más adelante.

CUENTA OPERATIVA (2026-08-06): Aunesa la manda como objeto
`{id: '57461ARS', denominacion: 'BANCO MARIVA TERCEROS'}`. Se aplana a la
DENOMINACIÓN sola (el id no le dice nada a nadie) y pasa a ser el eje del
resumen: una card por banco, no una por moneda. El id igual lleva la moneda
adentro, así que agrupamos por (denominación, unidad).

SALDO INICIAL: lo único que Aunesa NO da. Se carga a mano por banco y por día en
`operaciones.tesoreria_saldos` — con eso la card cierra en saldo final
(inicial + ingresos − egresos). Escritura restringida a la allowlist
`operaciones.tesoreria_escritores` (+ admin), gestionada en Manager → MESA.
"""
from __future__ import annotations

import logging
import unicodedata
from datetime import UTC, date, datetime, timedelta
from typing import Any

from psycopg.types.json import Jsonb

from api.services._sql import _q
from core import aunesa
from core.postgres import get_pool

_log = logging.getLogger(__name__)

_ENDPOINT = "cuentas/consultaMovDocsSolicitados"
# La dirección la da `solicitud`. Comparamos SIN acentos y en minúscula porque el string
# de Aunesa puede venir en NFC o NFD (la 'ó'/'ó' se ven iguales pero != por bytes) — comparar
# el literal acentuado directo descartaba TODAS las filas (200 OK con 0 resultados).
_INGRESO = "deposito"   # 'Depósito'
_EGRESO = "extraccion"  # 'Extracción'
PRESENCIA_TTL_S = 90    # visto hace ≤90s = conectado (el front pollea cada ~20s)


def _norm(s: Any) -> str:
    """Minúscula + sin diacríticos, para comparar `solicitud` a prueba de NFC/NFD."""
    d = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in d if not unicodedata.combining(c)).strip().lower()


def _hoy_art() -> datetime:
    """Ahora en ART (UTC-3), sin depender de la tz del server."""
    return datetime.now(UTC) - timedelta(hours=3)


def _dia(iso: str | None) -> date:
    """ISO YYYY-MM-DD → date; default = hoy ART."""
    return datetime.strptime(iso, "%Y-%m-%d").date() if iso else _hoy_art().date()


def _fechas(iso: str | None) -> tuple[str, str, str]:
    """(dd/mm/yyyy del día, dd/mm/yyyy del día+1, yyyymmdd del día) desde un ISO YYYY-MM-DD;
    default = hoy ART. El día+1 es para `liquidacionHasta`: Aunesa EXIGE desde < hasta
    (un rango de un solo día con desde==hasta tira 400), así que pedimos [día, día+1] y
    después filtramos las filas al día objetivo."""
    d = _dia(iso)
    return d.strftime("%d/%m/%Y"), (d + timedelta(days=1)).strftime("%d/%m/%Y"), d.strftime("%Y%m%d")


def _cuenta_operativa(v: Any) -> str:
    """El objeto `cuentaOperativa` de Aunesa → su denominación (el id no se muestra)."""
    if isinstance(v, dict):
        return str(v.get("denominacion") or v.get("id") or "").strip()
    return str(v or "").strip()


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _hora(id_: Any, yyyymmdd: str) -> str:
    """Hora HH:MM extraída del `id` (formato YYYYMMDDHHMMSS[ms]). '' si no matchea."""
    s = str(id_ or "")
    if len(s) >= 12 and s[:8] == yyyymmdd and s[8:12].isdigit():
        return f"{s[8:10]}:{s[10:12]}"
    return ""


def ingresos_egresos_dia(*, fecha: str | None = None, estado: str = "Procesado",
                         email: str = "") -> dict:
    """Ingresos/egresos bancarios de un día (default hoy ART) desde Aunesa.

    Devuelve:
      - `resumen`: {unidad: {ingresos, egresos, neto, n}} por moneda (ARS/USD).
      - `cuentas`: una entrada por CUENTA OPERATIVA (banco) × moneda, con el saldo
        inicial cargado a mano y el saldo final resultante.
      - `movimientos`: filas para la tabla (hora, cuenta, cliente, riel, unidad, tipo,
        monto, estado), ordenadas por hora desc.
    """
    dia = _dia(fecha)
    ddmmyyyy, ddmmyyyy_hasta, yyyymmdd = _fechas(fecha)
    marcar_presencia(email)  # pollear la vista ES el heartbeat
    params: dict[str, Any] = {"liquidacionDesde": ddmmyyyy, "liquidacionHasta": ddmmyyyy_hasta}
    if estado:
        params["estados"] = estado

    resp = aunesa.get(_ENDPOINT, params)
    if resp.status_code == 204:
        rows: list[dict] = []
    elif resp.status_code == 200:
        body = resp.json()
        rows = body if isinstance(body, list) else []
    else:
        raise RuntimeError(f"Aunesa {_ENDPOINT} [{resp.status_code}]: {resp.text[:300]}")

    resumen: dict[str, dict] = {}
    por_cuenta: dict[tuple[str, str], dict] = {}
    movimientos: list[dict] = []
    for r in rows:
        # El rango pedido es [día, día+1]; nos quedamos SOLO con las filas del día objetivo.
        if str(r.get("fecha") or "").strip() != ddmmyyyy:
            continue
        sol = _norm(r.get("solicitud"))
        es_ingreso, es_egreso = sol == _INGRESO, sol == _EGRESO
        cta = _cuenta_operativa(r.get("cuentaOperativa")) or "SIN CUENTA OPERATIVA"

        if es_ingreso or es_egreso:
            unidad = (r.get("unidad") or "?").upper()
            monto = _num(r.get("monto"))
            b = resumen.setdefault(unidad, {"ingresos": 0.0, "egresos": 0.0, "neto": 0.0, "n": 0})
            c = por_cuenta.setdefault(
                (cta, unidad),
                {"cuenta_operativa": cta, "unidad": unidad,
                 "ingresos": 0.0, "egresos": 0.0, "neto": 0.0, "n": 0},
            )
            for d in (b, c):
                if es_ingreso:
                    d["ingresos"] += monto
                else:
                    d["egresos"] += monto
                d["neto"] = d["ingresos"] - d["egresos"]
                d["n"] += 1

        # Movimiento = TODOS los campos crudos de Aunesa (persona aplanada a persona_*) +
        # derivados `_hora`/`_tipo`. Se devuelve todo para inspección directa en el front.
        mov = {k: v for k, v in r.items() if k != "persona"}
        for pk, pv in (r.get("persona") or {}).items():
            mov[f"persona_{pk}"] = pv
        mov["cuentaOperativa"] = cta
        mov["_hora"] = _hora(r.get("id"), yyyymmdd)
        mov["_tipo"] = "ingreso" if es_ingreso else "egreso" if es_egreso else ""
        movimientos.append(mov)

    for b in resumen.values():
        b["ingresos"], b["egresos"], b["neto"] = (
            round(b["ingresos"], 2), round(b["egresos"], 2), round(b["neto"], 2))
    movimientos.sort(key=lambda m: str(m.get("_hora") or ""), reverse=True)

    # Saldo inicial (carga manual) → saldo final de cada banco.
    saldos = _saldos_dia(dia)
    cuentas = []
    for clave, c in sorted(por_cuenta.items()):
        s = saldos.get(clave)
        ini = s["saldo_inicial"] if s else None
        c["ingresos"], c["egresos"], c["neto"] = (
            round(c["ingresos"], 2), round(c["egresos"], 2), round(c["neto"], 2))
        c["saldo_inicial"] = ini
        c["saldo_final"] = round(ini + c["neto"], 2) if ini is not None else None
        c["saldo_por"] = s["actualizado_por"] if s else None
        c["saldo_at"] = s["actualizado_at"] if s else None
        cuentas.append(c)

    return {"fecha": ddmmyyyy, "fecha_iso": dia.isoformat(), "estado": estado,
            "resumen": resumen, "cuentas": cuentas,
            "puede_editar_saldo": puede_editar_saldo(email),
            "conectados": conectados(), "actualizado_at": datetime.now(UTC).isoformat(),
            "movimientos": movimientos, "n": len(movimientos), "raw": len(movimientos)}


# ─────────────────────────────────────────────────────────────────────
# Presencia (quién tiene la vista abierta) — mismo patrón que SENEBIS
# ─────────────────────────────────────────────────────────────────────

def marcar_presencia(email: str) -> None:
    e = (email or "").lower().strip()
    if not e:
        return
    try:
        _exec("INSERT INTO operaciones.tesoreria_presencia (email, visto_at) "
              "VALUES (%(e)s, %(at)s) "
              "ON CONFLICT (email) DO UPDATE SET visto_at = EXCLUDED.visto_at",
              {"e": e, "at": datetime.now(UTC)})
    except Exception:
        _log.warning("tesoreria: no pude marcar presencia", exc_info=True)


def conectados() -> list[dict]:
    """Quiénes vieron la vista en los últimos PRESENCIA_TTL_S segundos."""
    try:
        rows = _q("SELECT email, visto_at FROM operaciones.tesoreria_presencia "
                  "WHERE visto_at >= now() - make_interval(secs => %(ttl)s) ORDER BY email",
                  {"ttl": PRESENCIA_TTL_S})
    except Exception:
        return []
    return [{"email": r["email"], "visto_at": r["visto_at"].isoformat()} for r in rows]


# ──────────────────────────────────────────────────────────────────────────────
# Saldo inicial por banco (carga manual — Aunesa solo da los movimientos del día)
# ──────────────────────────────────────────────────────────────────────────────

def _exec(sql: str, params: dict) -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        n = cur.rowcount
        conn.commit()
        return n


def _saldos_dia(dia: date) -> dict[tuple[str, str], dict]:
    """{(cuenta_operativa, unidad): {saldo_inicial, actualizado_por, actualizado_at}}.

    Degrada a vacío si la tabla todavía no existe (schema sin aplicar): el detalle
    de movimientos es lo crítico de la vista y no puede caerse por esto.
    """
    try:
        rows = _q("SELECT cuenta_operativa, unidad, saldo_inicial, actualizado_por, "
                  "actualizado_at FROM operaciones.tesoreria_saldos WHERE fecha = %(f)s",
                  {"f": dia})
    except Exception:
        _log.warning("tesoreria: no pude leer operaciones.tesoreria_saldos", exc_info=True)
        return {}
    return {
        (r["cuenta_operativa"], r["unidad"]): {
            "saldo_inicial": float(r["saldo_inicial"]) if r["saldo_inicial"] is not None else None,
            "actualizado_por": r["actualizado_por"],
            "actualizado_at": r["actualizado_at"].isoformat() if r["actualizado_at"] else None,
        }
        for r in rows
    }


def set_saldo_inicial(*, fecha: str | None, cuenta_operativa: str, unidad: str,
                      saldo: float | None, actor: str) -> dict:
    """Fija (o borra, con `saldo=None`) el saldo inicial de un banco para un día."""
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para editar saldos de Tesorería")
    cta = (cuenta_operativa or "").strip()
    uni = (unidad or "").strip().upper()
    if not cta or not uni:
        raise ValueError("faltan 'cuenta_operativa' y/o 'unidad'")
    dia = _dia(fecha)
    if saldo is None:
        _exec("DELETE FROM operaciones.tesoreria_saldos WHERE fecha = %(f)s "
              "AND cuenta_operativa = %(c)s AND unidad = %(u)s",
              {"f": dia, "c": cta, "u": uni})
    else:
        _exec(
            "INSERT INTO operaciones.tesoreria_saldos "
            "(fecha, cuenta_operativa, unidad, saldo_inicial, actualizado_por, actualizado_at) "
            "VALUES (%(f)s, %(c)s, %(u)s, %(s)s, %(por)s, %(at)s) "
            "ON CONFLICT (fecha, cuenta_operativa, unidad) DO UPDATE SET "
            "saldo_inicial = EXCLUDED.saldo_inicial, actualizado_por = EXCLUDED.actualizado_por, "
            "actualizado_at = EXCLUDED.actualizado_at",
            {"f": dia, "c": cta, "u": uni, "s": saldo,
             "por": (actor or "").lower() or None, "at": datetime.now(UTC)},
        )
    _audit(actor, "set_saldo_inicial", f"{dia.isoformat()}|{cta}|{uni}", {"saldo_inicial": saldo})
    return {"fecha": dia.isoformat(), "cuenta_operativa": cta, "unidad": uni,
            "saldo_inicial": saldo}


def _audit(actor: str, action: str, target: str, data: dict | None = None) -> None:
    """Evento de auditoría — nunca rompe la operación principal."""
    try:
        _exec(
            "INSERT INTO operaciones.tesoreria_audit (ts, actor, action, target, data) "
            "VALUES (%(ts)s, %(actor)s, %(action)s, %(target)s, %(data)s)",
            {"ts": datetime.now(UTC), "actor": (actor or "").lower() or None, "action": action,
             "target": target, "data": Jsonb(data or {})},
        )
    except Exception:
        _log.exception("tesoreria: audit insert falló")


# ──────────────────────────────────────────────────────────────────────────────
# Allowlist de escritura del saldo inicial (Manager → MESA). Default-deny.
# ──────────────────────────────────────────────────────────────────────────────

def puede_editar_saldo(email: str) -> bool:
    e = (email or "").lower().strip()
    if not e:
        return False
    try:
        from core.roles import get_user_role
        if get_user_role(e) == "admin":
            return True
        return bool(_q("SELECT 1 FROM operaciones.tesoreria_escritores WHERE email = %(e)s",
                       {"e": e}))
    except Exception:
        _log.warning("tesoreria: no pude resolver permiso de saldo", exc_info=True)
        return False


def listar_escritores() -> dict:
    rows = _q("SELECT email, agregado_por, agregado_at FROM operaciones.tesoreria_escritores "
              "ORDER BY email")
    return {"escritores": [
        {"email": r["email"], "agregado_por": r["agregado_por"],
         "agregado_at": r["agregado_at"].isoformat() if r["agregado_at"] else None}
        for r in rows
    ]}


def candidatos_escritores(q: str = "", limit: int = 30) -> dict:
    """Usuarios de la app que todavía no están en la allowlist de Tesorería."""
    rows = _q(
        "SELECT u.email, u.role FROM manager.manager_users u "
        "WHERE u.email NOT IN (SELECT email FROM operaciones.tesoreria_escritores) "
        "AND u.email ILIKE %(t)s ORDER BY u.email LIMIT %(lim)s",
        {"t": f"%{(q or '').strip()}%", "lim": limit},
    )
    return {"candidatos": rows}


def agregar_escritor(email: str, actor: str) -> dict:
    e = (email or "").lower().strip()
    if not e:
        raise ValueError("falta 'email'")
    _exec(
        "INSERT INTO operaciones.tesoreria_escritores (email, agregado_por, agregado_at) "
        "VALUES (%(e)s, %(por)s, %(at)s) ON CONFLICT (email) DO NOTHING",
        {"e": e, "por": (actor or "").lower() or None, "at": datetime.now(UTC)},
    )
    _audit(actor, "add_escritor", e)
    return {"email": e}


def quitar_escritor(email: str, actor: str) -> dict:
    e = (email or "").lower().strip()
    if not e:
        raise ValueError("falta 'email'")
    borrado = _exec("DELETE FROM operaciones.tesoreria_escritores WHERE email = %(e)s", {"e": e})
    _audit(actor, "remove_escritor", e)
    return {"borrado": borrado}
