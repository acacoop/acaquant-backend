"""Repositorio Opciones: loaders cacheados contra Opciones + metadata."""
from datetime import datetime, timedelta

import streamlit as st

from core.mongo import get_mongo_client_read
from dashboard.shared.db import get_db_opciones, get_meta_col


@st.cache_data(ttl=5, show_spinner=False)
def get_options_snapshot() -> list:
    """Snapshot actual de OptionsSnapshot (todo el book de GGAL)."""
    return list(get_db_opciones()["OptionsSnapshot"].find({}))


@st.cache_data(ttl=5, show_spinner=False)
def get_metadata() -> dict:
    """Devuelve dict {type: doc} con vr_ggal y config de la meta collection."""
    docs = list(get_meta_col().find({"type": {"$in": ["vr_ggal", "config"]}}))
    return {d.get("type"): d for d in docs}


@st.cache_data(ttl=120, show_spinner=False)
def fetch_estrategia_historico(symbols_key: frozenset, dias: int) -> list:
    """Histórico intradía de bid/offer/last para las patas de la estrategia."""
    desde = datetime.utcnow() - timedelta(days=dias)
    return list(get_db_opciones()["Data"].find(
        {"symbol": {"$in": list(symbols_key)}, "timestamp": {"$gte": desde}},
        {"_id": 0, "symbol": 1, "timestamp": 1, "last": 1, "bid": 1, "offer": 1},
    ))


@st.cache_data(ttl=300)
def fetch_vol_historico() -> list:
    """Rollup diario de Opciones.DataHistorica (alimentado por jobs.options_rollup).

    Devuelve filas (fecha, Strike, Tipo, EV_M) de los últimos 20 días.
    """
    fecha_min = (datetime.utcnow() - timedelta(days=20)).strftime("%Y-%m-%d")
    pipeline = [
        {"$match": {"fecha": {"$gte": fecha_min}, "ev": {"$gt": 0},
                    "strike": {"$exists": True}, "tipo": {"$exists": True}}},
        {"$group": {
            "_id": {"fecha": "$fecha", "strike": "$strike", "tipo": "$tipo"},
            "ev_total": {"$sum": "$ev"},
        }},
    ]
    docs = list(get_mongo_client_read()["Opciones"]["DataHistorica"].aggregate(pipeline))
    return [{
        "fecha":  d["_id"]["fecha"],
        "Strike": d["_id"]["strike"],
        "Tipo":   d["_id"]["tipo"],
        "EV_M":   round(d["ev_total"] / 1_000_000, 3),
    } for d in docs]
