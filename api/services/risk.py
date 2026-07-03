

"""Servicio RISK — datos de cuenta del broker (saldos, posiciones, márgenes).

Wrapper sobre `pyRofex.get_account_*`. Endpoints REST del broker bajo
`rest/risk/...`, de ahí el nombre del módulo.

Diseño:
  - Funciones puras (sin FastAPI). Las llama el router `/api/risk` y
    también pueden usarse desde scripts de smoke / debug.
  - Cache 3-5s para no machacar al broker cuando la UI hace polling.
  - Sesión pyRofex compartida con `services/ordenes` (singleton en
    `core/rofex_orders_session::ensure_session_envio`).

Datos sensibles → el router los gateia con módulo `operaciones`
(admin+trader, no sales). Ver api/main.py.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pyRofex

from api.cache import cached
from core.rofex_orders_session import ensure_session_envio, normalizar_cuenta

logger = logging.getLogger("api.services.risk")

# Mapeo rueda → settlement type del report. El report viene con sub-bloques
# por settlType: "0" = CI (mismo día), "2" = 24hs.
SETTLE_POR_RUEDA: dict[str, str] = {
    "CI":   "0",
    "24hs": "2",
}

# Moneda dentro de detailedCurrencyBalance que representa el dólar MEP.
# El broker distingue 8+ tipos de USD (D=MEP, C=CCL, G=Garantizado, etc.);
# este wrapper expone solo los relevantes para la operativa MEP.
USD_MEP_KEY = "USD D"
ARS_KEY = "ARS"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de parseo
# ─────────────────────────────────────────────────────────────────────────────


def _ms_to_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _resolve_account(account: str | None) -> str:
    """Inicializa la sesión y devuelve la cuenta efectiva. Si el caller
    no pasa account, se usa la del .env (cuenta_default)."""
    default = ensure_session_envio()
    return normalizar_cuenta(account or default)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints crudos
# ─────────────────────────────────────────────────────────────────────────────


def account_report(account: str | None = None) -> dict[str, Any]:
    """Reporte completo de la cuenta — saldos por moneda y por settlement,
    márgenes, portfolio. Cache 3s."""
    acc = _resolve_account(account)
    resp = pyRofex.get_account_report(account=acc)
    if not resp or resp.get("status") != "OK":
        raise RuntimeError(f"get_account_report devolvió status no-OK: {resp}")
    return resp


def account_positions(account: str | None = None) -> dict[str, Any]:
    """Posiciones (qué tickers tiene la cuenta y a qué precio promedio). Cache 5s."""
    acc = _resolve_account(account)
    resp = pyRofex.get_account_position(account=acc)
    if not resp or resp.get("status") != "OK":
        raise RuntimeError(f"get_account_position devolvió status no-OK: {resp}")
    return resp


def account_detailed_position(account: str | None = None) -> dict[str, Any]:
    """Posiciones detalladas por tipo de instrumento (BOND, NEGOTIABLE_OBLIGATION,
    etc.) con valuación a market. Cache 5s."""
    acc = _resolve_account(account)
    resp = pyRofex.get_detailed_position(account=acc)
    if not resp or resp.get("status") != "OK":
        raise RuntimeError(f"get_detailed_position devolvió status no-OK: {resp}")
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# Saldo parseado por rueda (lo que consume la UI de DOLAR MEP)
# ─────────────────────────────────────────────────────────────────────────────


def saldo_para_rueda(rueda: str = "CI", account: str | None = None) -> dict[str, Any]:
    """Saldos por moneda en una rueda — paridad con la vista "Posiciones" de Primary.

    Lee `accountData.detailedAccountReports[settle].currencyBalance.detailedCurrencyBalance`
    del report. Cada moneda trae:
        - available: efectivo disponible para esa moneda en esa rueda
        - consumed:  movimientos del día (con signo)

    Antes leíamos `availableToOperate.cash.detailedCash`, que es lo que el
    broker te DEJA OPERAR ahora (post-márgenes y movimientos pendientes), y
    no coincide con la columna "Efectivo Disponible" que muestra Primary.

    Returns:
        {
          account, rueda, settlement_type, settlement_date, last_calc,
          saldo_ars,        # = monedas["ARS"]["available"]
          saldo_usd_d,      # = monedas["USD D"]["available"]
          movimiento_ars,   # = monedas["ARS"]["consumed"]
          movimiento_usd_d, # = monedas["USD D"]["consumed"]
          monedas: { <code>: {"available": float, "consumed": float}, ... }
        }
    """
    if rueda not in SETTLE_POR_RUEDA:
        raise ValueError(f"rueda inválida: {rueda!r} (esperado: {sorted(SETTLE_POR_RUEDA)})")

    acc = _resolve_account(account)
    rpt = account_report(account=acc)
    settle_str = SETTLE_POR_RUEDA[rueda]

    account_data = rpt.get("accountData") or {}
    detailed = (account_data.get("detailedAccountReports") or {}).get(settle_str) or {}

    cb = (detailed.get("currencyBalance") or {}).get("detailedCurrencyBalance") or {}
    monedas: dict[str, dict[str, float | None]] = {
        codigo: {
            "available": (entry or {}).get("available"),
            "consumed":  (entry or {}).get("consumed"),
        }
        for codigo, entry in cb.items()
    }

    ars = monedas.get(ARS_KEY) or {}
    usd_d = monedas.get(USD_MEP_KEY) or {}

    return {
        "account":           acc,
        "rueda":             rueda,
        "settlement_type":   settle_str,
        "settlement_date":   _ms_to_iso(detailed.get("settlementDate")),
        "last_calc":         _ms_to_iso(account_data.get("lastCalculation")),
        "saldo_ars":         ars.get("available"),
        "saldo_usd_d":       usd_d.get("available"),
        "movimiento_ars":    ars.get("consumed"),
        "movimiento_usd_d":  usd_d.get("consumed"),
        "monedas":           monedas,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Listado de cuentas — populadas por jobs.descubrir_cuentas (read-only)
# ─────────────────────────────────────────────────────────────────────────────

DB_OPS = "Operaciones"
COL_ACCOUNTS = "AccountsDescubiertas"


@cached(ttl=600)
def _nombres_por_id_cuenta() -> dict[str, str]:
    """Mapa id_cuenta (str) → nombre del titular desde SQL `clientes.cuentas.denominacion`
    (fuente COMPLETA: TODAS las cuentas sincronizadas de Aunesa). Antes salía solo de
    accionistas/contrapartes (un subconjunto chico) → la mayoría de las cuentas quedaba
    SIN nombre. `denominacion` puede venir '[N] NOMBRE' o 'NOMBRE' a secas → se normaliza.
    """
    import re

    from core.postgres import get_pool
    out: dict[str, str] = {}
    _re_cta = re.compile(r"^\[(\d+)\]\s*(.*)$")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_cuenta, denominacion FROM clientes.cuentas "
                    "WHERE denominacion IS NOT NULL AND denominacion <> ''")
        for idc, deno in cur.fetchall():
            s = str(deno).strip()
            m = _re_cta.match(s)
            nombre = (m.group(2).strip() if m else s)
            if idc and nombre:
                out[str(idc)] = nombre
    return out


def listado_cuentas(solo_activas: bool = False) -> list[dict[str, Any]]:
    """Cuentas para operar = ESPEJO de las comitentes de SQL `clientes.cuentas`.

    NO hay descubrimiento al broker (se eliminó `jobs.descubrir_cuentas` +
    `Operaciones.AccountsDescubiertas`): el universo operable son las cuentas comitentes
    que ya están en `clientes`, mantenidas al día por el cron diario `jobs.sync_comitentes`
    (Aunesa). Una cuenta comitente nueva aparece sola al día siguiente. SQL-only, sin Mongo.

    Devuelve `account_id` + `nombre` (denominacion normalizada — viene '[N] NOMBRE' o 'NOMBRE').
    `solo_activas` se ignora (compat de firma; clientes.cuentas ya son las activas de Aunesa).

    Returns: [{"account_id": "100", "nombre": "Acme S.A."}, ...]  (ordenado por id_cuenta)
    """
    import re

    from core.postgres import get_pool
    _re_cta = re.compile(r"^\[(\d+)\]\s*(.*)$")
    out: list[dict[str, Any]] = []
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_cuenta, denominacion FROM clientes.cuentas "
                    "WHERE id_cuenta ~ '^[0-9]+$' ORDER BY id_cuenta::int")
        for idc, deno in cur.fetchall():
            s = str(deno or "").strip()
            m = _re_cta.match(s)
            out.append({
                # ROFEX exige mínimo 3 dígitos: '9' → '009'. El id_cuenta de clientes.cuentas
                # perdió los ceros (bug de sync) → sin esto ROFEX no vincula ni trae saldos.
                "account_id":         normalizar_cuenta(str(idc)),
                "nombre":             (m.group(2).strip() if m else s) or None,
                # Shape compat (el front lee estos): ya NO hay snapshot de saldos del broker.
                # Toda comitente es operable → activa=True; los saldos se piden en vivo aparte.
                "ars_disponible":     None,
                "usd_d_disponible":   None,
                "n_posiciones":       0,
                "activa":             True,
                "last_discovered_at": None,
            })
    return out
