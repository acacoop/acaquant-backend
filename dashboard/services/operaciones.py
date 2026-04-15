"""Servicios Operaciones: lógica pura sobre movimientos y flujo. Sin Mongo ni Streamlit."""
import re

import pandas as pd

_COOP_RE = re.compile(r"\bcoop", re.IGNORECASE)


def es_cooperativa(cuenta_str) -> bool:
    """Detecta cooperativas por nombre (regex \\bcoop, case-insensitive)."""
    return bool(cuenta_str) and bool(_COOP_RE.search(str(cuenta_str)))


def filtrar_movimientos(
    df: pd.DataFrame,
    rango: tuple,
    monedas_sel: list,
    filtro_acc: str,
    seleccion: str,
    acc_map: dict,
) -> pd.DataFrame:
    """Aplica filtros de Cash Flow: rango fecha, monedas, filtro accionistas + cuenta/accionista/coop.

    filtro_acc: "Todas" | "Sin accionistas" | "Solo accionistas" | "Solo cooperativas".
    seleccion: valor del dropdown secundario ("Todas"/"Todos" o valor específico).
    """
    df_f = df[(df["fecha"].dt.date >= rango[0]) & (df["fecha"].dt.date <= rango[1])].copy()
    df_f = df_f[df_f["unidad"].isin(monedas_sel)].copy()

    df_f["_accionista"] = df_f["cuenta"].map(acc_map)
    if filtro_acc == "Sin accionistas":
        df_f = df_f[df_f["_accionista"].isna()].copy()
        if seleccion != "Todas":
            df_f = df_f[df_f["cuenta"] == seleccion].copy()
    elif filtro_acc == "Solo accionistas":
        df_f = df_f[df_f["_accionista"].notna()].copy()
        if seleccion != "Todos":
            df_f = df_f[df_f["_accionista"] == seleccion].copy()
    elif filtro_acc == "Solo cooperativas":
        df_f = df_f[df_f["_accionista"].isna() & df_f["cuenta"].apply(es_cooperativa)].copy()
        if seleccion != "Todas":
            df_f = df_f[df_f["cuenta"] == seleccion].copy()
    elif seleccion != "Todas":
        df_f = df_f[df_f["cuenta"] == seleccion].copy()

    return df_f
