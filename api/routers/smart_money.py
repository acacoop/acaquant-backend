"""Router /api/smart-money — endpoints del módulo Renta Variable.

Consumido por el frontend acaquant-web /renta-variable. Todos los endpoints
son GET, read-only, RBAC `renta-variable` (default visible para admin/trader/sales).

Endpoints:

  GET /api/smart-money/managers                  → lista de managers descubiertos
  GET /api/smart-money/manager/{cik}             → portfolio del manager con Q-on-Q
  GET /api/smart-money/ticker/{ticker}           → flow completo del CEDEAR (13F + Form 4)
  GET /api/smart-money/cohort-overview           → top buys/sells, consensus, divergences
  GET /api/smart-money/recent-activity?days=7    → últimas N días de Form 4
  GET /api/smart-money/catalog                   → catálogo de CEDEARs activos
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import require_module
from api.deps import get_db_smart_read
from api.services import smart_money as svc

router = APIRouter(
    prefix="/api/smart-money",
    tags=["SmartMoney"],
    dependencies=[Depends(require_module("renta-variable"))],
)


@router.get("/managers")
def managers_list():
    """Lista de managers institucionales descubiertos por el ingest 13F.

    Cada uno con: cik, name, last_filing_date, n_filings_seen, max_n_cedear_holdings.
    Sorted por last_filing_date descendente.
    """
    return svc.get_managers_list()


@router.get("/manager/{cik}")
def manager_portfolio(cik: str):
    """Portfolio CEDEAR de un manager — último quarter + Q-on-Q diffs.

    Cada holding viene con status (NEW/INCREASED/REDUCED/UNCHANGED) y delta %.
    """
    res = svc.get_manager_portfolio(cik)
    if isinstance(res, dict) and res.get("error"):
        raise HTTPException(status_code=404, detail=res["error"])
    return res


@router.get("/ticker/{ticker}")
def ticker_flow(ticker: str):
    """Flow completo sobre un CEDEAR — 13F institucional + Form 4 insiders.

    Devuelve:
      - meta: ticker / cusip / nombre / cik_issuer
      - institutional_13f: top holders, Q-on-Q (new/increased/reduced/exited)
      - insiders_form4: top trades últimos 90 días + agregados (buy / sell / net)
    """
    res = svc.get_ticker_flow(ticker)
    if isinstance(res, dict) and res.get("error"):
        raise HTTPException(status_code=404, detail=res["error"])
    return res


@router.get("/cohort-overview")
def cohort_overview():
    """Top buys / sells / consensus / divergences del cohort agregado."""
    return svc.get_cohort_overview()


@router.get("/recent-activity")
def recent_activity(days: int = Query(7, ge=1, le=90)):
    """Últimas N días de Form 4 transactions. Cap: 90 días."""
    return svc.get_recent_activity(days=days)


@router.get("/catalog")
def cedears_catalog():
    """Catálogo de CEDEARs activos (los 36 tickers que filtramos)."""
    db = get_db_smart_read()
    return list(db["CEDEARsCatalog"].find(
        {"is_active": True},
        {"_id": 0},
    ).sort("ticker", 1))
