"""Capa de servicio — cotizaciones.

Funciones puras (sin FastAPI, sin HTTP) que hacen el trabajo de acceso a
Mongo + transformación. Llamadas desde:

1. `api/routers/cotizaciones.py` — handlers FastAPI (thin wrappers).
2. `api/agent/tools.py::dispatch` — llamada directa, sin loopback HTTP.

Beneficios vs. llamada HTTP loopback:
- ~100-300ms menos por turn del asistente (sin DNS localhost + socket +
  re-parse FastAPI + verify_api_key + re-serialize JSON).
- Tests deterministas del agente sin levantar uvicorn.
- Errores llegan como excepciones tipadas, no como status codes.

El `@cached(ttl=N)` vive acá: así ambos callers (router + dispatch directo)
comparten el cache hit.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from api.cache import cached
from api.db import get_db_opciones, get_db_trading, get_db_valuaciones
from core.mongo import get_mongo_client


def _ticker_filter(instrumento: str) -> dict:
    """Regex substring escape — compat con ticker corto o completo."""
    return {"$regex": re.escape(instrumento), "$options": "i"}


def resolver_ticker_exacto(instrumento: str) -> str | None:
    """Resuelve un ticker (corto o completo) al ticker completo de ROFEX.

    Evita regex table-scan cuando se puede usar match exacto (índice
    (ticker, timestamp) en TimeSales). Si el input ya trae ' - ', se asume
    completo. Si es corto, se busca en Trading.Curvas.ticker_corto.

    Devuelve el ticker completo o None si no se pudo resolver.
    """
    instr = (instrumento or "").strip()
    if not instr:
        return None
    if " - " in instr:
        return instr
    db = get_db_trading()
    doc = db["Curvas"].find_one(
        {"ticker_corto": instr}, {"ticker": 1, "_id": 0}
    )
    return doc.get("ticker") if doc else None


# ─────────────────────────────────────────────────────────────────────────────
# Series BCRA (BADLAR, CER, DOLAR)
# ─────────────────────────────────────────────────────────────────────────────


def _query_serie(collection: str, desde: str | None, hasta: str | None) -> list:
    db = get_db_trading()
    filtro: dict = {}
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["fecha"] = rango
    return list(
        db[collection]
        .find(filtro, {"_id": 0, "fecha": 1, "valor": 1})
        .sort("fecha", 1)
    )


@cached(ttl=3600)
def get_badlar(desde: str | None = None, hasta: str | None = None) -> list:
    return _query_serie("BADLAR", desde, hasta)


@cached(ttl=3600)
def get_cer(desde: str | None = None, hasta: str | None = None) -> list:
    return _query_serie("CER", desde, hasta)


@cached(ttl=3600)
def get_dolar(desde: str | None = None, hasta: str | None = None) -> list:
    return _query_serie("DOLAR", desde, hasta)


# ─────────────────────────────────────────────────────────────────────────────
# Dólar MEP
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=30)
def get_ultimo_mep() -> dict:
    """Último valor del dólar MEP (Valuaciones.Dolar)."""
    db = get_db_valuaciones()
    doc = db["Dolar"].find_one(
        {}, {"_id": 0, "mep": 1, "timestamp": 1},
        sort=[("timestamp", -1)],
    )
    return doc or {}


@cached(ttl=300)
def get_historico_mep(desde: str | None = None, hasta: str | None = None) -> list:
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


# ─────────────────────────────────────────────────────────────────────────────
# Forwards
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=30)
def get_forwards(curva: str | None = None) -> list:
    db = get_db_trading()
    filtro: dict = {}
    if curva:
        filtro["curva"] = curva
    return list(db["ForwardsLive"].find(filtro, {"_id": 0}))


@cached(ttl=300)
def get_historico_forwards(
    curva: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
) -> list:
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


# ─────────────────────────────────────────────────────────────────────────────
# Breakevens
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=30)
def get_breakevens() -> list:
    db = get_db_trading()
    return list(db["BreakevensLive"].find({}, {"_id": 0}))


@cached(ttl=300)
def get_historico_breakevens(desde: str | None = None, hasta: str | None = None) -> list:
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


# ─────────────────────────────────────────────────────────────────────────────
# Renta Fija (MarketSnapshot)
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=5)
def get_renta_fija(instrumento: str | None = None) -> list:
    db = get_db_trading()
    filtro: dict = {}
    if instrumento:
        filtro["ticker"] = _ticker_filter(instrumento)

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


# ─────────────────────────────────────────────────────────────────────────────
# Opciones
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=10)
def get_opciones(instrumento: str | None = None, tipo: str | None = None) -> list:
    db = get_db_opciones()
    filtro: dict = {}
    if instrumento:
        filtro["symbol"] = _ticker_filter(instrumento)
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


@cached(ttl=60)
def get_opciones_meta() -> dict:
    """Metadata opciones: tasa libre de riesgo + VR (ADR/local)."""
    db = get_db_opciones()
    docs = list(db["Metadata"].find(
        {"type": {"$in": ["vr_ggal", "config"]}}, {"_id": 0}
    ))
    by_type = {d.get("type"): d for d in docs}
    vr = by_type.get("vr_ggal") or {}
    cfg = by_type.get("config") or {}
    return {
        "tasa": float(cfg.get("tasa") or 0.0),
        "vr_local": float(vr.get("vr_local") or 0.0),
        "vr_adr": float(vr.get("vr_adr") or 0.0),
        "updated_at": vr.get("updated_at"),
    }


@cached(ttl=30)
def get_historico_opciones(
    instrumento: str | None = None,
    tipo: str | None = None,
) -> list:
    """Trades de opciones de los últimos 21 días (Opciones.Data)."""
    db = get_db_opciones()
    corte = datetime.now() - timedelta(days=21)
    filtro: dict = {"timestamp": {"$gte": corte}}
    if instrumento:
        filtro["symbol"] = instrumento
    if tipo:
        filtro["tipo"] = tipo.upper()

    pipeline = [
        {"$match": filtro},
        {"$sort": {"timestamp": -1}},
        {"$limit": 5000},
        {"$project": {
            "_id": 0,
            "instrumento": "$symbol",
            "timestamp": 1,
            "last_timestamp": 1,
            "bid": 1,
            "offer": 1,
            "last": 1,
            "spot": 1,
            "strike": 1,
            "tipo": 1,
            "iv": 1,
            "delta": 1,
            "gamma": 1,
            "vega": 1,
            "theta": 1,
        }},
    ]
    return list(db["Data"].aggregate(pipeline))


def update_opciones_tasa(valor: float) -> dict:
    """Actualiza la tasa libre de riesgo en Opciones.Metadata.config.
    Post-update hace clear_cache() (no decorator — es mutación)."""
    from api.cache import clear_cache
    col = get_mongo_client()["Opciones"]["Metadata"]
    col.update_one(
        {"type": "config"},
        {"$set": {"tasa": float(valor)}},
        upsert=True,
    )
    clear_cache()
    return {"ok": True, "tasa": float(valor)}


# ─────────────────────────────────────────────────────────────────────────────
# Histórico de trades + curvas (Trading.TimeSales)
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=15)
def get_historico_trades(instrumento: str | None = None) -> list:
    """Trades de los últimos 15 días. Match EXACTO por ticker para usar índice."""
    db = get_db_trading()
    corte = datetime.now(UTC) - timedelta(days=15)
    filtro: dict = {"timestamp": {"$gte": corte}}
    if instrumento:
        exacto = resolver_ticker_exacto(instrumento)
        if exacto is None:
            return []
        filtro["ticker"] = exacto

    pipeline = [
        {"$match": filtro},
        {"$sort": {"timestamp": -1}},
        {"$limit": 10000},
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


@cached(ttl=300)
def get_historico_curva(curva: str) -> list:
    """Serie diaria por ticker de una curva: último precio + enriquecimiento."""
    db = get_db_trading()
    meta = {
        d["ticker"]: d.get("ticker_corto") or d["ticker"]
        for d in db["Curvas"].find({"curva": curva}, {"ticker": 1, "ticker_corto": 1})
    }
    if not meta:
        return []

    pipeline = [
        {"$match": {"ticker": {"$in": list(meta.keys())}, "price": {"$gt": 0}}},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id": {
                "ticker": "$ticker",
                "fecha": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
            },
            "price": {"$first": "$price"},
            "TEA": {"$first": "$TEA"},
            "TEM": {"$first": "$TEM"},
            "duration": {"$first": "$duration"},
            "paridad": {"$first": "$paridad"},
        }},
        {"$sort": {"_id.fecha": 1, "_id.ticker": 1}},
    ]
    out = []
    for r in db["TimeSales"].aggregate(pipeline):
        t_full = r["_id"]["ticker"]
        out.append({
            "fecha": r["_id"]["fecha"],
            "ticker": meta.get(t_full, t_full),
            "price": r.get("price"),
            "TEA": r.get("TEA"),
            "TEM": r.get("TEM"),
            "duration": r.get("duration"),
            "paridad": r.get("paridad"),
        })
    return out
