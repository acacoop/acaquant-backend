"""Capa de servicio — opciones (chain + meta + trades históricos + update tasa).

Todas las funciones atacan la DB `Opciones` (poblada por `engines/options.py`
vía WS + `jobs/options_rollup.py` al cierre). La única mutación es
`update_opciones_tasa`, que escribe la tasa libre de riesgo en
`Opciones.Metadata.config` y limpia el cache in-process.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from api.cache import cached
from api.db import get_db_opciones
from api.services.renta_fija import _ticker_filter
from core.mongo import get_mongo_client


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
