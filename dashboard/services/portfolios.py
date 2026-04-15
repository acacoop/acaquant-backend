"""Servicios Portfolios: reglas de negocio sobre Carteras/CarterasII/Assets.

Funciones puras sobre DataFrames. No accede a Mongo ni a Streamlit directamente:
usa la capa `dashboard.repos.portfolios` para obtener datos.
"""
import pandas as pd

from dashboard.repos.portfolios import get_assets, get_carteras, get_carteras_ii

_ASSET_COLS = ["TICKER", "EMISOR", "CLASE_ACTIVO", "CARTERA", "CALIFICACION", "VENCIMIENTO"]


def _merge_with_assets(df: pd.DataFrame) -> pd.DataFrame:
    """Enriquece un DataFrame con columnas de Assets por 'unidad'."""
    assets_df = get_assets()
    if not assets_df.empty:
        df = df.merge(assets_df, on="unidad", how="left")
    for col in _ASSET_COLS:
        if col in df.columns:
            df[col] = df[col].fillna("-")
    return df


def _calcular_valuacion(row) -> float:
    # Regla dominio: FCI y OTROS → P×Q directo. Resto (TP, ONs, Letras, etc.) → P×Q/100.
    cantidad = row["cantidad"]
    precio = row["precio_num"]
    clase = row.get("CLASE_ACTIVO", "") or ""
    cartera = str(row.get("CARTERA", "") or "")
    if clase == "OTROS" or "FCI" in cartera:
        return cantidad * precio
    return cantidad * precio / 100


def build_carteras_enriquecidas() -> pd.DataFrame:
    """Carteras mes actual con join Assets + columna 'valuación' calculada."""
    df = get_carteras()
    if df.empty:
        return df
    df = df.copy()
    df["precio_num"] = pd.to_numeric(df["precio"], errors="coerce")
    df["cantidad"] = pd.to_numeric(df["cantidad"], errors="coerce").fillna(0)
    df = _merge_with_assets(df)
    df["valuación"] = df.apply(_calcular_valuacion, axis=1)
    return df


def build_carteras_ii_enriquecidas() -> pd.DataFrame:
    """CarterasII (mes anterior) con join Assets. 'valuación' viene pre-calculada en el snapshot."""
    df = get_carteras_ii()
    if df.empty:
        return df
    df = df.copy()
    df["valuación"] = pd.to_numeric(df.get("valuacion"), errors="coerce").fillna(0)
    return _merge_with_assets(df)
