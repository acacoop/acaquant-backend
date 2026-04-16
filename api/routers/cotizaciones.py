"""Router Cotizaciones: lectura directa de Trading.* (sin migración)."""
from fastapi import APIRouter, Query

from api.deps import get_db_trading

router = APIRouter(prefix="/api/cotizaciones", tags=["Cotizaciones"])


# ── Series BCRA (BADLAR, CER, DOLAR) ──

@router.get("/badlar")
def listar_badlar(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    return _query_serie("BADLAR", desde, hasta)


@router.get("/cer")
def listar_cer(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    return _query_serie("CER", desde, hasta)


@router.get("/dolar")
def listar_dolar(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    return _query_serie("DOLAR", desde, hasta)


def _query_serie(collection: str, desde: str | None, hasta: str | None) -> list:
    db = get_db_trading()
    filtro = {}
    if desde or hasta:
        rango = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["fecha"] = rango
    return list(db[collection].find(filtro, {"_id": 0}))


# ── Forwards Live ──

@router.get("/forwards")
def listar_forwards(
    curva: str | None = Query(None, description="Filtrar por curva (tasa_fija/cer)"),
):
    db = get_db_trading()
    filtro = {}
    if curva:
        filtro["curva"] = curva
    return list(db["ForwardsLive"].find(filtro, {"_id": 0}))


# ── Market Snapshot ──

@router.get("/mercado")
def listar_mercado(
    ticker: str | None = Query(None, description="Filtrar por ticker (ej: MERV - XMEV - S30A6 - 24hs)"),
):
    db = get_db_trading()
    filtro = {}
    if ticker:
        filtro["ticker"] = ticker

    projection = {
        "_id": 0,
        "ticker": 1,
        "book": 1,
        "metrics.total_nominals": 1,
        "metrics.vwap": 1,
        "metrics.last_price": 1,
        "metrics.open_price": 1,
        "metrics.high_price": 1,
        "metrics.low_price": 1,
        "metrics.closing_price": 1,
        "recent_trades": 1,
    }
    return list(db["MarketSnapshot"].find(filtro, projection))


# ── Breakevens Live ──

@router.get("/breakevens")
def listar_breakevens():
    db = get_db_trading()
    return list(db["BreakevensLive"].find({}, {"_id": 0}))
