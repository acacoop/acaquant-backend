"""Router Cotizaciones: lectura directa de Trading.* y Opciones.* (sin migración)."""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query

from api.deps import get_db_opciones, get_db_trading, get_db_valuaciones

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


# ── Dólar MEP ──

@router.get("/mep")
def ultimo_mep():
    """Último valor del dólar MEP (Valuaciones.Dolar)."""
    db = get_db_valuaciones()
    doc = db["Dolar"].find_one(
        {}, {"_id": 0, "mep": 1, "timestamp": 1},
        sort=[("timestamp", -1)],
    )
    return doc or {}


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


# ── Renta Fija (ex Market Snapshot) ──

@router.get("/renta-fija")
def listar_renta_fija(
    instrumento: str | None = Query(None, description="Filtrar por instrumento (ej: MERV - XMEV - S30A6 - 24hs)"),
):
    db = get_db_trading()
    filtro = {}
    if instrumento:
        filtro["ticker"] = instrumento

    pipeline = [
        {"$match": filtro},
        {"$project": {
            "_id": 0,
            "instrumento": "$ticker",
            "book": 1,
            "metrics.total_nominals": 1,
            "metrics.vwap": 1,
            "metrics.last_price": 1,
            "metrics.open_price": 1,
            "metrics.high_price": 1,
            "metrics.low_price": 1,
            "metrics.closing_price": 1,
            "recent_trades": 1,
        }},
    ]
    return list(db["MarketSnapshot"].aggregate(pipeline))


# ── Breakevens Live ──

@router.get("/breakevens")
def listar_breakevens():
    db = get_db_trading()
    return list(db["BreakevensLive"].find({}, {"_id": 0}))


# ── Opciones (OptionsSnapshot) ──

@router.get("/opciones")
def listar_opciones(
    instrumento: str | None = Query(None, description="Filtrar por instrumento (ej: MERV - XMEV - GFGC10950A - 24hs)"),
    tipo: str | None = Query(None, description="Filtrar por tipo (CALL/PUT)"),
):
    db = get_db_opciones()
    filtro = {}
    if instrumento:
        filtro["symbol"] = instrumento
    if tipo:
        filtro["tipo"] = tipo.upper()

    pipeline = [
        {"$match": filtro},
        {"$project": {
            "_id": 0,
            "instrumento": "$symbol",
            "bid": 1,
            "offer": 1,
            "last": 1,
            "open": 1,
            "high": 1,
            "low": 1,
            "ev": 1,
            "spot": 1,
            "strike": 1,
            "tipo": 1,
            "vence": 1,
            "closing_price": 1,
            "delta": 1,
            "gamma": 1,
            "iv": 1,
            "theta": 1,
            "vega": 1,
            "updated_at": 1,
        }},
    ]
    return list(db["OptionsSnapshot"].aggregate(pipeline))


# ── Histórico ──

@router.get("/historico/forwards")
def historico_forwards(
    curva: str | None = Query(None, description="Filtrar por curva (tasa_fija/cer)"),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    db = get_db_trading()
    filtro: dict = {}
    if curva:
        filtro["curva"] = curva
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["fecha"] = rango
    return list(db["ForwardsHistorico"].find(filtro, {"_id": 0}))


@router.get("/historico/breakevens")
def historico_breakevens(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    db = get_db_trading()
    filtro: dict = {}
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["fecha"] = rango
    return list(db["BreakevensHistorico"].find(filtro, {"_id": 0}))


@router.get("/historico/mep")
def historico_mep(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    """Serie histórica del dólar MEP (Valuaciones.Dolar)."""
    db = get_db_valuaciones()
    filtro: dict = {}
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = datetime.fromisoformat(desde)
        if hasta:
            rango["$lte"] = datetime.fromisoformat(hasta + "T23:59:59")
        filtro["timestamp"] = rango
    return list(db["Dolar"].find(filtro, {"_id": 0, "mep": 1, "timestamp": 1}))


@router.get("/historico/trades")
def historico_trades(
    instrumento: str | None = Query(None, description="Filtrar por instrumento (ej: MERV - XMEV - TX26 - 24hs)"),
):
    """Trades de los últimos 15 días desde hoy."""
    db = get_db_trading()
    corte = datetime.now(UTC) - timedelta(days=15)
    filtro: dict = {"timestamp": {"$gte": corte}}
    if instrumento:
        filtro["ticker"] = instrumento

    pipeline = [
        {"$match": filtro},
        {"$sort": {"timestamp": -1}},
        {"$project": {
            "_id": 0,
            "instrumento": "$ticker",
            "timestamp": 1,
            "price": 1,
            "size": 1,
            "side": 1,
            "money": 1,
            "duration": 1,
            "TEA": 1,
            "TEM": 1,
            "paridad": 1,
        }},
    ]
    return list(db["TimeSales"].aggregate(pipeline))
