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

from core.rofex_orders_session import cuenta_default, ensure_session_envio

logger = logging.getLogger("api.services.risk")

# Mapeo rueda → settlement type del report. El report viene con sub-bloques
# por settlType: "0" = CI (mismo día), "2" = 24hs.
SETTLE_POR_RUEDA: dict[str, str] = {
    "CI":   "0",
    "24hs": "2",
}

# Moneda dentro de detailedCash que representa el dólar MEP. El broker
# distingue 8+ tipos de USD (D=MEP, C=CCL, G=Garantizado, etc.); este
# wrapper expone solo el relevante para la operativa MEP.
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
    """Saldo ARS y USD MEP (USD D) disponible para operar en una rueda.

    Lee `accountData.detailedAccountReports[settle].availableToOperate.cash.detailedCash`
    del report. Devuelve None en saldo_ars / saldo_usd_d si la moneda
    no aparece (caso poco probable — el broker siempre lista las claves).

    Returns:
        {
          account, rueda, settlement_type, settlement_date, last_calc,
          saldo_ars,        # ARS disponible (puede ser negativo)
          saldo_usd_d,      # USD MEP disponible (puede ser negativo)
          total_cash,       # totalCash del bloque (suma de todas las monedas)
        }
    """
    if rueda not in SETTLE_POR_RUEDA:
        raise ValueError(f"rueda inválida: {rueda!r} (esperado: {sorted(SETTLE_POR_RUEDA)})")

    acc = _resolve_account(account)
    rpt = account_report(account=acc)
    settle_str = SETTLE_POR_RUEDA[rueda]

    account_data = rpt.get("accountData") or {}
    detailed = (account_data.get("detailedAccountReports") or {}).get(settle_str) or {}

    cash_block = (detailed.get("availableToOperate") or {}).get("cash") or {}
    detailed_cash = cash_block.get("detailedCash") or {}

    saldo_ars = detailed_cash.get(ARS_KEY)
    saldo_usd_d = detailed_cash.get(USD_MEP_KEY)
    total_cash = cash_block.get("totalCash")

    return {
        "account":          acc,
        "rueda":            rueda,
        "settlement_type":  settle_str,
        "settlement_date":  _ms_to_iso(detailed.get("settlementDate")),
        "last_calc":        _ms_to_iso(account_data.get("lastCalculation")),
        "saldo_ars":        saldo_ars,
        "saldo_usd_d":      saldo_usd_d,
        "total_cash":       total_cash,
    }


def cuenta_efectiva(account: str | None = None) -> str:
    """Helper para que el router resuelva account=None contra la default.
    NO inicializa la sesión — solo devuelve el nombre."""
    return account or cuenta_default()
