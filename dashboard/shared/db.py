"""Accesos cacheados a las DBs de MongoDB (read-only) para el dashboard."""
import streamlit as st
from core.mongo import get_mongo_client_read


@st.cache_resource(ttl=3600)
def get_db():
    """DB Trading (microstructure, curvas, forwards, breakevens)."""
    return get_mongo_client_read()["Trading"]


@st.cache_resource(ttl=3600)
def get_db_opciones():
    return get_mongo_client_read()["Opciones"]


@st.cache_resource(ttl=3600)
def get_meta_col():
    return get_mongo_client_read()["Opciones"]["Metadata"]


@st.cache_resource(ttl=3600)
def get_db_valuaciones():
    return get_mongo_client_read()["Valuaciones"]


@st.cache_resource(ttl=3600)
def get_db_cashflow():
    return get_mongo_client_read()["CashFlow"]


@st.cache_data(ttl=3600, show_spinner=False)
def _cargar_tickers_merv():
    """Lista de tickers MERV desde Trading.Curvas, ordenados por fecha_vencimiento."""
    docs = list(get_db()["Curvas"].find(
        {"ticker": {"$exists": True}},
        {"_id": 0, "ticker": 1, "fecha_vencimiento": 1},
    ))
    docs.sort(key=lambda d: d.get("fecha_vencimiento", ""))
    return [d["ticker"] for d in docs if d.get("ticker")]
