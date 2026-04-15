"""Repositorio Portfolios: acceso crudo a Mongo para la vista Reportes.

Devuelve DataFrames/primitivos. No aplica lógica de negocio.
"""
import pandas as pd
import streamlit as st

from dashboard.shared.db import get_db, get_db_valuaciones


@st.cache_data(ttl=300, show_spinner=False)
def get_dolar_oficial() -> float | None:
    """Último valor de Trading.DOLAR (A3500)."""
    doc = get_db()["DOLAR"].find_one(sort=[("fecha", -1)])
    return float(doc["valor"]) if doc else None


@st.cache_data(ttl=60, show_spinner=False)
def get_valor_mep() -> float | None:
    """Último valor MEP desde Valuaciones.Dolar."""
    doc = get_db_valuaciones()["Dolar"].find_one(sort=[("timestamp", -1)])
    if not doc or "mep" not in doc:
        return None
    try:
        return float(doc["mep"])
    except (TypeError, ValueError):
        return None


@st.cache_data(ttl=600, show_spinner=False)
def get_assets() -> pd.DataFrame:
    """Valuaciones.Assets con columnas usadas en Reportes."""
    docs = list(get_db_valuaciones()["Assets"].find(
        {}, {"_id": 0, "unidad": 1, "CALIFICACION": 1, "CARTERA": 1,
             "CLASE_ACTIVO": 1, "EMISOR": 1, "TICKER": 1, "VENCIMIENTO": 1}
    ))
    return pd.DataFrame(docs) if docs else pd.DataFrame()


@st.cache_data(ttl=120, show_spinner=False)
def get_carteras() -> pd.DataFrame:
    """Valuaciones.Carteras crudo (sin join, sin cálculo)."""
    docs = list(get_db_valuaciones()["Carteras"].find({}, {"_id": 0}))
    return pd.DataFrame(docs) if docs else pd.DataFrame()


@st.cache_data(ttl=600, show_spinner=False)
def get_carteras_ii() -> pd.DataFrame:
    """Valuaciones.CarterasII crudo (snapshot primer día hábil mes anterior)."""
    docs = list(get_db_valuaciones()["CarterasII"].find({}, {"_id": 0}))
    return pd.DataFrame(docs) if docs else pd.DataFrame()
