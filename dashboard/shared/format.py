"""Helpers de formato compartidos por todas las vistas del dashboard."""
from datetime import timedelta
import streamlit as st


def short_name(ticker):
    parts = ticker.split(" - ")
    return parts[2] if len(parts) >= 3 else ticker


def fmt_money(v):
    v = v or 0
    if v >= 1_000_000_000: return f"${v/1_000_000_000:.1f}B"
    if v >= 1_000_000:     return f"${v/1_000_000:.1f}M"
    if v >= 1_000:         return f"${v/1_000:.0f}K"
    return f"${v:.0f}"


def fmt_nom(v):
    v = v or 0
    if v >= 1_000_000_000: return f"{v/1_000_000_000:.2f}B"
    if v >= 1_000_000:     return f"{v/1_000_000:.1f}M"
    if v >= 1_000:         return f"{v/1_000:.0f}K"
    return f"{v:.0f}"


def fmt_vol(v):
    if not v or v == 0: return "-"
    if v >= 1_000_000: return f"{v/1_000_000:.1f}M"
    if v >= 1_000:     return f"{v/1_000:.0f}k"
    return f"{v:.0f}"


def df_height(nrows, max_h=800):
    """Altura en píxeles para que el dataframe muestre todas las filas sin scroll vertical."""
    return min(38 + 35 * nrows, max_h)


def last_update_badge(ts):
    """Muestra la hora de última actualización en horario Argentina (UTC-3). Sin contadores."""
    if not ts:
        return
    # ts viene como UTC-naive desde MongoDB; restamos 3h para obtener ART
    ts_art = ts - timedelta(hours=3)
    st.caption(f"Última actualización: {ts_art.strftime('%H:%M:%S')}")
