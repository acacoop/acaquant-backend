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
from core.rofex_orders_session import cuenta_default, ensure_session_envio

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
    return account or default


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


def cuenta_efectiva(account: str | None = None) -> str:
    """Helper para que el router resuelva account=None contra la default.
    NO inicializa la sesión — solo devuelve el nombre."""
    return account or cuenta_default()


# ─────────────────────────────────────────────────────────────────────────────
# Listado de cuentas — populadas por jobs.descubrir_cuentas (read-only)
# ─────────────────────────────────────────────────────────────────────────────

DB_OPS = "Operaciones"
COL_ACCOUNTS = "AccountsDescubiertas"


@cached(ttl=600)
def _nombres_por_id_cuenta() -> dict[str, str]:
    """Mapa id_cuenta (str) → nombre del titular. DIRECTO desde las fuentes
    CashFlow.Accionistas / CashFlow.Contrapartes (sin los espejos CuentasAPI.*).
    Cache TTL 10min porque cambian poco (alta de cliente ~1x/sem).

    Accionistas: `cuenta` = '[N] NOMBRE' → id_cuenta y nombre se derivan.
    Contrapartes: `cuenta` = id, `contraparte` = nombre. Si un id vive en
    ambas, prevalece Accionistas (fuente canónica).
    """
    import re

    from core.mongo import get_mongo_client_read
    cf = get_mongo_client_read()["CashFlow"]
    out: dict[str, str] = {}
    _re_cta = re.compile(r"^\[(\d+)\]\s*(.*)$")
    for d in cf["Accionistas"].find({}, {"_id": 0, "cuenta": 1}):
        m = _re_cta.match(str(d.get("cuenta") or "").strip())
        if m and m.group(2).strip():
            out[m.group(1)] = m.group(2).strip()
    for d in cf["Contrapartes"].find({}, {"_id": 0, "cuenta": 1, "contraparte": 1}):
        idc = d.get("cuenta")
        nom = d.get("contraparte")
        if idc and nom:
            out.setdefault(str(idc), str(nom))
    return out


def listado_cuentas(solo_activas: bool = False) -> list[dict[str, Any]]:
    """Lee Operaciones.AccountsDescubiertas y devuelve la lista para el
    dropdown del frontend.

    NO pega al broker — la colección la mantiene `jobs.descubrir_cuentas`
    (un backfill que corre 1 vez/día por cron). Ordenadas por ARS
    disponible descendente (las gordas arriba). El nombre del titular se
    joinea de CuentasAPI.AccionistasAPI / ContrapartesAPI.

    Returns:
        [
          {
            "account_id": "100",
            "nombre": "Acme S.A.",          # del join con CuentasAPI
            "ars_disponible": 3007793886.49,
            "usd_d_disponible": 4237.87,
            "n_posiciones": 8,
            "activa": True,
            "last_discovered_at": "2026-04-28T11:30:00+00:00"
          },
          ...
        ]
    """
    from core.mongo import get_mongo_client_read

    filtro: dict[str, Any] = {}
    if solo_activas:
        filtro["activa"] = True

    col = get_mongo_client_read()[DB_OPS][COL_ACCOUNTS]
    cursor = col.find(filtro, {"_id": 0})
    docs = list(cursor)
    nombres = _nombres_por_id_cuenta()

    out: list[dict[str, Any]] = []
    for d in docs:
        snap = d.get("last_snapshot") or {}
        ts = d.get("last_discovered_at")
        acc = d.get("account_id")
        out.append({
            "account_id":         acc,
            "nombre":             nombres.get(str(acc)) if acc else None,
            "ars_disponible":     snap.get("ars_disponible"),
            "usd_d_disponible":   snap.get("usd_d_disponible"),
            "n_posiciones":       snap.get("n_posiciones") or 0,
            "activa":             bool(d.get("activa")),
            "last_discovered_at": ts.isoformat() if isinstance(ts, datetime) else ts,
        })

    # Ordenar por ARS descendente (las cuentas gordas primero); las de
    # ARS=None / 0 al final pero antes que las negativas.
    def _key(c):
        ars = c.get("ars_disponible")
        if ars is None:
            return (1, 0)
        return (0, -ars)

    out.sort(key=_key)
    return out
