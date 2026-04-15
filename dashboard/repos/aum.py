"""Repositorio AuM: loaders cacheados contra Valuaciones + Trading.Curvas."""
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import streamlit as st

from dashboard.shared.db import get_db, get_db_valuaciones


def parallel(*fns):
    """Ejecuta funciones sin args en paralelo; devuelve resultados en orden.

    Aprovecha que los loaders cacheados son independientes y cada query contra
    Atlas pasa ~180ms esperando la red: se solapan en vez de encadenarse.
    Seguro con @st.cache_data (es thread-safe).
    """
    with ThreadPoolExecutor(max_workers=len(fns)) as ex:
        futures = [ex.submit(fn) for fn in fns]
        return [f.result() for f in futures]


@st.cache_data(ttl=300, show_spinner=False)
def get_aum_ultimo() -> pd.DataFrame:
    """Último snapshot de AuM (todas las carteras). Para tabs Tasa Fija / CER / RV."""
    db = get_db_valuaciones()
    last = db["AuM"].find_one(
        {}, {"fecha_snapshot": 1, "_id": 0},
        sort=[("fecha_snapshot", -1)],
    )
    if not last:
        return pd.DataFrame()
    fecha = last["fecha_snapshot"]
    docs = list(db["AuM"].find(
        {"fecha_snapshot": fecha},
        {"_id": 0, "id_cuenta": 1, "cuenta": 1, "unidad": 1,
         "cantidad": 1, "valuacion": 1, "fecha_snapshot": 1},
    ))
    if not docs:
        return pd.DataFrame()
    df = pd.DataFrame(docs)
    df["valuacion"] = pd.to_numeric(df["valuacion"], errors="coerce").fillna(0)
    df["cantidad"] = pd.to_numeric(df["cantidad"], errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=600, show_spinner=False)
def get_fci_unidades() -> list:
    db = get_db_valuaciones()
    return [
        a["unidad"]
        for a in db["Assets"].find(
            {"CARTERA": "CARTERA FCI"}, {"unidad": 1, "_id": 0}
        )
        if a.get("unidad")
    ]


@st.cache_data(ttl=300, show_spinner=False)
def get_aum_fci_agg() -> pd.DataFrame:
    """Histórico agregado (fecha_snapshot, unidad) → suma de valuación.

    Lee de `Valuaciones.AuMResumenFCI` (rollup pre-materializado por
    `jobs/aum_resumen_fci.py`). Esquema: un doc por fecha con array de unidades
    adentro, para minimizar transporte por cursor (~22 docs en vez de ~2.4k).
    """
    db = get_db_valuaciones()
    docs = list(db["AuMResumenFCI"].find(
        {}, {"_id": 0, "fecha_snapshot": 1, "unidades": 1}
    ))
    if not docs:
        return pd.DataFrame()
    rows = [
        {"fecha_snapshot": d["fecha_snapshot"],
         "unidad":         u["unidad"],
         "valuacion":      u["valuacion_total"]}
        for d in docs
        for u in d.get("unidades", [])
    ]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["valuacion"] = pd.to_numeric(df["valuacion"], errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=300, show_spinner=False)
def get_aum_fci_snapshot(fecha) -> pd.DataFrame:
    """Raw docs FCI para una fecha puntual. Drill-down emisor → ticker → cuenta."""
    if not fecha:
        return pd.DataFrame()
    db = get_db_valuaciones()
    unidades = get_fci_unidades()
    if not unidades:
        return pd.DataFrame()
    docs = list(db["AuM"].find(
        {"unidad": {"$in": unidades}, "fecha_snapshot": fecha},
        {"_id": 0, "cuenta": 1, "unidad": 1, "valuacion": 1, "fecha_snapshot": 1},
    ))
    if not docs:
        return pd.DataFrame()
    df = pd.DataFrame(docs)
    df["valuacion"] = pd.to_numeric(df["valuacion"], errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=300, show_spinner=False)
def get_assets_map() -> dict:
    """Dict {unidad: {CARTERA, EMISOR, TICKER, CLASE_ACTIVO, CALIFICACION, VENCIMIENTO}}."""
    db = get_db_valuaciones()
    docs = list(db["Assets"].find({}, {"_id": 0, "unidad": 1,
        "CARTERA": 1, "EMISOR": 1, "TICKER": 1,
        "CLASE_ACTIVO": 1, "CALIFICACION": 1, "VENCIMIENTO": 1}))
    return {d["unidad"]: d for d in docs}


@st.cache_data(ttl=300, show_spinner=False)
def get_curvas_tasa_fija() -> dict:
    """Dict {ticker_corto: {fecha_vencimiento, flujo_vencimiento}} para curva=tasa_fija."""
    docs = list(get_db()["Curvas"].find(
        {"curva": "tasa_fija"},
        {"_id": 0, "ticker_corto": 1, "fecha_vencimiento": 1, "flujo_vencimiento": 1},
    ))
    return {d["ticker_corto"]: d for d in docs}


@st.cache_data(ttl=300, show_spinner=False)
def get_curvas_cer() -> dict:
    """Dict {ticker_corto: {fecha_vencimiento}} para curva=cer."""
    docs = list(get_db()["Curvas"].find(
        {"curva": "cer"},
        {"_id": 0, "ticker_corto": 1, "fecha_vencimiento": 1},
    ))
    return {d["ticker_corto"]: d for d in docs}
