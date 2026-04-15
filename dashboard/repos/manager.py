"""Repositorio Manager: I/O puro para diagnóstico y benchmarks de latencia.

El Manager es un panel de admin y la mayoría de sus lecturas se hacen fresh
(sin cache) — por eso este módulo concentra los fetchers puros y los traces
de latencia, sin Streamlit ni formateo UI.
"""
from datetime import datetime, timedelta

import pandas as pd

from core.mongo import get_mongo_client
from core.profiler import Stopwatch


def fetch_latest(db_n: str, coll_n: str, field: str, filtro: dict) -> dict | None:
    """Devuelve el doc con el mayor valor de `field` para (db, coll) bajo `filtro`."""
    coll = get_mongo_client()[db_n][coll_n]
    return coll.find_one(filtro, sort=[(field, -1)], projection={field: 1})


# ─── TRACES DE LATENCIA ─────────────────────────────────────────────────────
# Replican la carga de cada vista con un Stopwatch inyectado en cada etapa
# (mongo, pandas, altair) para descomponer el tiempo real. Ignoran la cache
# de Streamlit — cada trace mide trabajo real, no hits de cache.

def trace_aum_fci(client) -> dict:
    import altair as alt
    db_val = client["Valuaciones"]
    sw = Stopwatch("AuM — FCI")

    sw.step("mongo: find ultimo fecha_snapshot")
    last = db_val["AuM"].find_one({}, {"fecha_snapshot": 1}, sort=[("fecha_snapshot", -1)])
    fecha = last["fecha_snapshot"] if last else None

    sw.step("mongo: find Assets (CARTERA FCI)")
    unidades = [
        a["unidad"]
        for a in db_val["Assets"].find({"CARTERA": "CARTERA FCI"}, {"unidad": 1, "_id": 0})
        if a.get("unidad")
    ]

    sw.step("mongo: find AuMResumenFCI (rollup 1 doc/fecha)")
    docs_hist = list(db_val["AuMResumenFCI"].find(
        {}, {"_id": 0, "fecha_snapshot": 1, "unidades": 1}
    ))

    sw.step("mongo: find AuM snapshot último (FCI)")
    docs_snap = list(db_val["AuM"].find(
        {"unidad": {"$in": unidades}, "fecha_snapshot": fecha},
        {"_id": 0, "cuenta": 1, "unidad": 1, "valuacion": 1, "fecha_snapshot": 1},
    ))

    sw.step("pandas: DataFrames + conversiones")
    rows_hist = [
        {"fecha_snapshot": d["fecha_snapshot"],
         "unidad":         u["unidad"],
         "valuacion":      u["valuacion_total"]}
        for d in docs_hist
        for u in d.get("unidades", [])
    ]
    df_hist = pd.DataFrame(rows_hist)
    _ = pd.DataFrame(docs_snap)
    if not df_hist.empty:
        df_hist["valuacion"] = pd.to_numeric(df_hist["valuacion"], errors="coerce").fillna(0)

    sw.step("altair: build chart evolución")
    _ = (alt.Chart(df_hist).mark_line()
         .encode(x="fecha_snapshot:T", y="valuacion:Q", color="unidad:N")
         if not df_hist.empty else None)

    return sw.done()


def trace_operaciones_cashflow(client) -> dict:
    db_cf = client["CashFlow"]
    sw = Stopwatch("Operaciones — Cash Flow")

    sw.step("mongo: find Movimientos (full)")
    docs = list(db_cf["Movimientos"].find(
        {}, {"_id": 0, "fecha": 1, "total": 1, "unidad": 1, "informacion": 1, "cuenta": 1}
    ))

    sw.step("mongo: find Accionistas")
    _ = list(db_cf["Accionistas"].find({}, {"_id": 0, "cuenta": 1, "accionista": 1}))

    sw.step("pandas: DataFrame + parse fechas + sort")
    df = pd.DataFrame(docs)
    if not df.empty:
        df["fecha"] = pd.to_datetime(df["fecha"], format="%d/%m/%Y", errors="coerce")
        df["total"] = pd.to_numeric(df["total"], errors="coerce").fillna(0)
        df = df.dropna(subset=["fecha"]).sort_values("fecha")

    sw.step("pandas: groupby mes × unidad")
    if not df.empty:
        df["_key"] = df["fecha"].dt.strftime("%Y-%m")
        _ = df.groupby(["_key", "unidad"], as_index=False)["total"].sum()

    return sw.done()


def trace_mercado_libro(client) -> dict:
    db_tr = client["Trading"]
    sw = Stopwatch("Mercado — Libro (1 ticker)")

    sw.step("mongo: find_one MarketSnapshot (primer ticker)")
    first = db_tr["MarketSnapshot"].find_one({}, {"ticker": 1, "_id": 0})
    ticker = first["ticker"] if first else None

    sw.step("mongo: find_one snapshot completo")
    _ = db_tr["MarketSnapshot"].find_one({"ticker": ticker}) if ticker else None

    sw.step("mongo: find TimeSales últimos 60 min (ticker)")
    desde = datetime.utcnow() - timedelta(minutes=60)
    if ticker:
        trades = list(db_tr["TimeSales"].find(
            {"ticker": ticker, "timestamp": {"$gte": desde}},
            {"_id": 0, "timestamp": 1, "price": 1, "size": 1},
        ))
    else:
        trades = []

    sw.step("pandas: DataFrame trades")
    df = pd.DataFrame(trades)
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"])

    return sw.done()


def trace_opciones_mercado(client) -> dict:
    sw = Stopwatch("Opciones — Mercado")

    sw.step("mongo: find OptionsSnapshot (full)")
    _ = list(client["Opciones"]["OptionsSnapshot"].find({}, {"_id": 0}))

    sw.step("mongo: agg vol histórico DataHistorica (20d)")
    fecha_min = (datetime.utcnow() - timedelta(days=20)).strftime("%Y-%m-%d")
    pipeline = [
        {"$match": {"fecha": {"$gte": fecha_min}, "ev": {"$gt": 0},
                    "strike": {"$exists": True}, "tipo": {"$exists": True}}},
        {"$group": {
            "_id": {"fecha": "$fecha", "strike": "$strike", "tipo": "$tipo"},
            "ev_total": {"$sum": "$ev"},
        }},
    ]
    rows = list(client["Opciones"]["DataHistorica"].aggregate(pipeline))

    sw.step("pandas: DataFrame agg")
    _ = pd.DataFrame([
        {"fecha": d["_id"]["fecha"], "Strike": d["_id"]["strike"],
         "Tipo": d["_id"]["tipo"], "EV_M": d["ev_total"] / 1_000_000}
        for d in rows
    ])

    return sw.done()


TRACES = {
    "AuM — FCI":                trace_aum_fci,
    "Operaciones — Cash Flow":  trace_operaciones_cashflow,
    "Mercado — Libro":          trace_mercado_libro,
    "Opciones — Mercado":       trace_opciones_mercado,
}
