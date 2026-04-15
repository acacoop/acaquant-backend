"""Repositorio Mercado: loaders cacheados (breakevens, forwards, volúmenes, retorno)."""
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from dashboard.shared.db import get_db


@st.cache_data(ttl=60, show_spinner=False)
def get_breakevens_historico() -> list:
    db = get_db()
    return list(db["BreakevensHistorico"].find(
        {},
        {"fecha": 1, "pares": 1, "_id": 0}
    ))


@st.cache_data(ttl=60, show_spinner=False)
def get_datos_simulador() -> tuple:
    """Datos para el simulador de breakevens: curvas, CER, días hábiles, últimos precios."""
    db = get_db()
    curvas = list(db["Curvas"].find({}))
    cer_docs = list(db["CER"].find({}, {"fecha": 1, "valor": 1, "_id": 0}))
    dias_habiles = sorted(d["fecha"] for d in db["DiasHabiles"].find({}, {"fecha": 1, "_id": 0}))
    snaps = list(db["MarketSnapshot"].find({}, {"ticker": 1, "metrics": 1, "_id": 0}))
    last_price = {}
    for s in snaps:
        t = s.get("ticker")
        p = (s.get("metrics") or {}).get("last_price")
        if t and p:
            last_price[t] = float(p)
    cer_dict = {d["fecha"]: float(d["valor"]) for d in cer_docs}
    return curvas, cer_dict, dias_habiles, last_price


@st.cache_data(ttl=60, show_spinner=False)
def get_forwards_historico(curva: str) -> list:
    db = get_db()
    return list(db["ForwardsHistorico"].find(
        {"curva": curva},
        {"fecha": 1, "matrix": 1, "_id": 0}
    ))


@st.cache_data(ttl=300, show_spinner=False)
def get_tickers_curvas() -> pd.DataFrame:
    """Tickers en Trading.Curvas con su label corto."""
    db = get_db()
    rows = [
        {"ticker": d["ticker"],
         "ticker_corto": d.get("ticker_corto") or d["ticker"],
         "curva": d.get("curva", "")}
        for d in db["Curvas"].find({}, {"ticker": 1, "ticker_corto": 1, "curva": 1})
    ]
    return pd.DataFrame(rows).sort_values("ticker_corto").reset_index(drop=True)


@st.cache_data(ttl=300, show_spinner=False)
def get_volumen_diario_tickers(tickers_key: tuple) -> pd.DataFrame:
    """Suma de money por (fecha, ticker) en los últimos 15 días corridos."""
    if not tickers_key:
        return pd.DataFrame()
    db = get_db()
    fecha_min = datetime.utcnow() - timedelta(days=15)
    pipeline = [
        {"$match": {"ticker": {"$in": list(tickers_key)},
                    "money": {"$gt": 0},
                    "timestamp": {"$gte": fecha_min}}},
        {"$group": {
            "_id": {
                "fecha":  {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
                "ticker": "$ticker",
            },
            "money": {"$sum": "$money"},
        }},
    ]
    rows = [
        {"fecha": r["_id"]["fecha"], "ticker": r["_id"]["ticker"], "money": r["money"]}
        for r in db["TimeSales"].aggregate(pipeline)
    ]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["money_mm"] = df["money"] / 1_000_000
    return df.sort_values(["fecha", "ticker"])


@st.cache_data(ttl=300, show_spinner=False)
def get_precios_intraday(tickers_key: tuple) -> pd.DataFrame:
    """Último precio por minuto (downsample server-side) para los últimos 15 días corridos."""
    if not tickers_key:
        return pd.DataFrame()
    db = get_db()
    fecha_min = datetime.utcnow() - timedelta(days=15)
    pipeline = [
        {"$match": {"ticker": {"$in": list(tickers_key)},
                    "price": {"$gt": 0},
                    "timestamp": {"$gte": fecha_min}}},
        {"$sort": {"timestamp": 1}},
        {"$group": {
            "_id": {
                "ticker": "$ticker",
                "bucket": {"$dateTrunc": {"date": "$timestamp", "unit": "minute"}},
            },
            "price": {"$last": "$price"},
        }},
    ]
    rows = [
        {"ticker": r["_id"]["ticker"],
         "timestamp": r["_id"]["bucket"],
         "price": r["price"]}
        for r in db["TimeSales"].aggregate(pipeline)
    ]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.sort_values("timestamp")


@st.cache_data(ttl=300, show_spinner=False)
def get_precios_diarios_curva(curva: str) -> pd.DataFrame:
    """Último precio por ticker por día para todos los instrumentos de una curva.

    Retorna DataFrame largo con columnas: fecha (str), ticker (ticker_corto), price.
    """
    db = get_db()
    meta = {
        d["ticker"]: d["ticker_corto"]
        for d in db["Curvas"].find({"curva": curva}, {"ticker": 1, "ticker_corto": 1})
    }
    if not meta:
        return pd.DataFrame()

    pipeline = [
        {"$match": {"ticker": {"$in": list(meta.keys())}, "price": {"$gt": 0}}},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id": {
                "ticker": "$ticker",
                "fecha": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
            },
            "price": {"$first": "$price"},
        }},
    ]
    rows = [
        {"fecha": r["_id"]["fecha"], "ticker": meta[r["_id"]["ticker"]], "price": r["price"]}
        for r in db["TimeSales"].aggregate(pipeline)
        if r["_id"]["ticker"] in meta
    ]
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["ticker", "fecha"]).reset_index(drop=True)
