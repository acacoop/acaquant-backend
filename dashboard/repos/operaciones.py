"""Repositorio Operaciones: acceso cacheado a Mongo para Cash Flow, Contrapartes y AuM."""
from datetime import datetime

import pandas as pd
import streamlit as st

from dashboard.shared.db import get_db_cashflow, get_db_valuaciones


@st.cache_data(ttl=900, show_spinner=False)
def get_movimientos() -> pd.DataFrame:
    """CashFlow.Movimientos filtrado server-side por año (últimos ~24 meses).

    `fecha` viene como string "dd/mm/yyyy"; usamos regex sobre el año para
    acotar la transferencia desde Mongo.
    """
    db = get_db_cashflow()
    y_now = datetime.utcnow().year
    years = [str(y_now - i) for i in range(3)]
    pat = rf"/({'|'.join(years)})$"
    docs = list(db["Movimientos"].find(
        {"fecha": {"$regex": pat}},
        {"_id": 0, "fecha": 1, "total": 1, "unidad": 1, "informacion": 1, "cuenta": 1},
    ))
    if not docs:
        return pd.DataFrame()
    df = pd.DataFrame(docs)
    df["fecha"] = pd.to_datetime(df["fecha"], format="%d/%m/%Y", errors="coerce")
    df["total"] = pd.to_numeric(df["total"], errors="coerce").fillna(0)
    return df.dropna(subset=["fecha"]).sort_values("fecha")


@st.cache_data(ttl=600, show_spinner=False)
def get_accionistas_map() -> dict:
    """Dict {cuenta: accionista} desde CashFlow.Accionistas."""
    db = get_db_cashflow()
    docs = list(db["Accionistas"].find({}, {"_id": 0, "cuenta": 1, "accionista": 1}))
    return {d["cuenta"]: d["accionista"] for d in docs if "cuenta" in d}


@st.cache_data(ttl=900, show_spinner=False)
def get_flujo_contrapartes() -> pd.DataFrame:
    """CashFlow.Flujo con segmento joined desde CashFlow.Contrapartes."""
    db = get_db_cashflow()
    docs = list(db["Flujo"].find(
        {}, {"_id": 0, "bruto": 1, "concertacion": 1, "contraparte": 1, "moneda": 1, "tipoOperacion": 1}
    ))
    if not docs:
        return pd.DataFrame()
    df = pd.DataFrame(docs)
    df["concertacion"] = pd.to_datetime(df["concertacion"], errors="coerce")
    df["bruto"] = pd.to_numeric(df["bruto"], errors="coerce").fillna(0)
    df = df.dropna(subset=["concertacion"]).sort_values("concertacion")

    cp_docs = list(db["Contrapartes"].find({}, {"_id": 0, "contraparte": 1, "segmento": 1}))
    seg_map = {d["contraparte"]: d.get("segmento") or "Sin clasificar" for d in cp_docs}
    df["segmento"] = df["contraparte"].map(seg_map).fillna("Sin clasificar")

    return df


@st.cache_data(ttl=900, show_spinner=False)
def get_fondos_flujo_aum() -> tuple[list, pd.DataFrame, pd.DataFrame]:
    """Devuelve (fondos, df_flujo, df_aum) para la tab Flujo vs AuM.

    - fondos:   contrapartes con segmento=Fondos
    - df_flujo: (emisor, fecha, bruto) — trades ARS individuales
    - df_aum:   (emisor, fecha_snapshot, valuacion) — AuM diario por emisor
    """
    db_cf = get_db_cashflow()
    db_val = get_db_valuaciones()

    fondos = list(dict.fromkeys(
        d["contraparte"] for d in db_cf["Contrapartes"].find(
            {"segmento": "Fondos"}, {"_id": 0, "contraparte": 1}
        )
    ))
    if not fondos:
        return [], pd.DataFrame(), pd.DataFrame()

    flujo_docs = list(db_cf["Flujo"].find(
        {"contraparte": {"$in": fondos}, "moneda": "ARS"},
        {"_id": 0, "contraparte": 1, "concertacion": 1, "bruto": 1}
    ))
    if flujo_docs:
        df_fl = pd.DataFrame(flujo_docs)
        df_fl["fecha"] = pd.to_datetime(df_fl["concertacion"], errors="coerce")
        df_fl["bruto"] = pd.to_numeric(df_fl["bruto"], errors="coerce").fillna(0)
        df_fl["emisor"] = df_fl["contraparte"]
        df_flujo = df_fl[["emisor", "fecha", "bruto"]].dropna(subset=["fecha"]).sort_values("fecha")
    else:
        df_flujo = pd.DataFrame()

    assets_docs = list(db_val["Assets"].find(
        {"EMISOR": {"$in": fondos}, "CARTERA": "CARTERA FCI"},
        {"_id": 0, "unidad": 1, "EMISOR": 1}
    ))
    if assets_docs:
        df_assets = pd.DataFrame(assets_docs)
        unidades = df_assets["unidad"].tolist()
        emisor_map = df_assets.set_index("unidad")["EMISOR"].to_dict()
        # $group server-side: colapsa cuentas por (unidad, fecha) antes del transfer.
        # Ventana de 36 meses para acotar el cache (fecha_snapshot es ISO "YYYY-MM-DD").
        desde_snap = (datetime.utcnow() - pd.Timedelta(days=365 * 3)).strftime("%Y-%m-%d")
        aum_rows = list(db_val["AuM"].aggregate([
            {"$match":   {"unidad": {"$in": unidades},
                          "fecha_snapshot": {"$gte": desde_snap}}},
            {"$group":   {"_id": {"u": "$unidad", "f": "$fecha_snapshot"},
                          "valuacion": {"$sum": "$valuacion"}}},
            {"$project": {"_id": 0, "unidad": "$_id.u",
                          "fecha_snapshot": "$_id.f", "valuacion": 1}},
        ]))
        if aum_rows:
            df_a = pd.DataFrame(aum_rows)
            df_a["valuacion"] = pd.to_numeric(df_a["valuacion"], errors="coerce").fillna(0)
            df_a["fecha_snapshot"] = pd.to_datetime(df_a["fecha_snapshot"], errors="coerce")
            df_a["emisor"] = df_a["unidad"].map(emisor_map)
            df_aum = (
                df_a.dropna(subset=["fecha_snapshot", "emisor"])
                .groupby(["emisor", "fecha_snapshot"], as_index=False)["valuacion"]
                .sum()
                .sort_values("fecha_snapshot")
            )
        else:
            df_aum = pd.DataFrame()
    else:
        df_aum = pd.DataFrame()

    return fondos, df_flujo, df_aum
