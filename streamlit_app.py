import streamlit as st
import pandas as pd
import altair as alt
import re
import math
import requests
from datetime import datetime, timedelta
from mongo_manager import get_mongo_client, get_mongo_client_read
from config import MANAGER_EMAILS
from Opciones.calculos_cuantitativos import bs_price as _bs_price
import config
from views.data_manager import vista_data_manager

# ==========================================
# CONFIG
# ==========================================
st.set_page_config(
    layout="wide",
    page_title="ACAQuant | Mesa de Dinero",
    page_icon="📈",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
[data-testid="stSidebar"] {
    background-color: #094293;
}
[data-testid="stSidebar"] * {
    color: #ffffff !important;
}
[data-testid="stSidebar"] .stRadio label {
    color: #ffffff !important;
}
[data-testid="stSidebar"] hr {
    border-color: rgba(255,255,255,0.3);
}
/* Fondo blanco solo en el bloque donde vive la imagen del logo */
[data-testid="stSidebar"] [data-testid="stImage"] {
    background-color: #ffffff;
    padding: 12px;
    border-radius: 0 0 8px 8px;
}
</style>
""", unsafe_allow_html=True)

def get_user_email() -> str:
    """
    Lee el email autenticado por Cloudflare Access del header HTTP.
    En desarrollo local (sin Cloudflare) devuelve string vacío.
    """
    try:
        headers = st.context.headers
        return headers.get("Cf-Access-Authenticated-User-Email", "").strip().lower()
    except Exception:
        return ""


def is_manager_allowed() -> bool:
    """Devuelve True si el usuario actual tiene acceso al Manager."""
    if not MANAGER_EMAILS:
        return True  # Si no hay lista configurada, permite acceso (modo dev local)
    return get_user_email() in MANAGER_EMAILS


def short_name(ticker):
    parts = ticker.split(" - ")
    return parts[2] if len(parts) >= 3 else ticker


# ==========================================
# CONEXIÓN A MONGO (cached, una sola vez)
# ==========================================
@st.cache_resource(ttl=3600)
def get_db():
    return get_mongo_client_read()["Trading"]


@st.cache_data(ttl=3600, show_spinner=False)
def _cargar_tickers_merv():
    """Lista de tickers MERV desde Trading.Curvas, ordenados por fecha_vencimiento."""
    docs = list(get_db()["Curvas"].find(
        {"ticker": {"$exists": True}},
        {"_id": 0, "ticker": 1, "fecha_vencimiento": 1},
    ))
    docs.sort(key=lambda d: d.get("fecha_vencimiento", ""))
    return [d["ticker"] for d in docs if d.get("ticker")]

@st.cache_resource(ttl=3600)
def get_db_opciones():
    return get_mongo_client_read()["Opciones"]

@st.cache_resource(ttl=3600)
def get_meta_col():
    return get_mongo_client_read()["Opciones"]["Metadata"]

@st.cache_resource(ttl=3600)
def get_db_valuaciones():
    return get_mongo_client_read()["Valuaciones"]


# ==========================================
# SIDEBAR - NAVEGACIÓN
# ==========================================
with st.sidebar:
    st.image("images/logo-header.png", use_container_width=True)
    st.markdown("---")
    _opciones_nav = ["Mercado", "Opciones", "Portfolios", "Operaciones", "AuM"]
    if is_manager_allowed():
        _opciones_nav.append("Manager")
    vista = st.radio(
        "Vista",
        _opciones_nav,
        label_visibility="collapsed"
    )


# ==========================================
# HELPERS
# ==========================================
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


# ==========================================
# RENDER FUNCTIONS — LIBRO
# ==========================================
def render_depth(book):
    bids   = book.get("bids", [])
    offers = book.get("offers", [])
    rows = []
    for i in range(5):
        rows.append({
            "Bid Q": f"{bids[i]['size']:,.0f}"    if i < len(bids)   else "-",
            "Bid P": bids[i]['price']              if i < len(bids)   else None,
            "Ask P": offers[i]['price']            if i < len(offers) else None,
            "Ask Q": f"{offers[i]['size']:,.0f}"   if i < len(offers) else "-",
        })
    df = pd.DataFrame(rows)
    styler = (
        df.style
        .map(lambda v: "color: #00cc66; font-weight: bold" if pd.notna(v) else "", subset=["Bid P"])
        .map(lambda v: "color: #ff4444; font-weight: bold" if pd.notna(v) else "", subset=["Ask P"])
        .format({
            "Bid P": lambda v: f"{v:,.2f}" if pd.notna(v) else "-",
            "Ask P": lambda v: f"{v:,.2f}" if pd.notna(v) else "-",
        })
    )
    st.caption("DEPTH")
    st.dataframe(styler, hide_index=True, use_container_width=True, height=df_height(5))


def render_quant(m):
    closing   = m.get('closing_price', 0) or 0
    last      = m.get('last_price', 0) or 0
    open_     = m.get('open_price', 0) or 0
    vs_cierre = (last / closing - 1) if closing > 0 and last > 0 else None
    intraday  = (last / open_ - 1)   if open_  > 0 and last > 0 else None

    rows = [
        ("Total Money",     fmt_money(m.get('total_money', 0))),
        ("Buy Session",     fmt_money(m.get('buy_money', 0))),
        ("Sell Session",    fmt_money(m.get('sell_money', 0))),
        ("VWAP (Daily)",    f"${m.get('vwap', 0):,.2f}"),
        ("Micro-Price",     f"{m.get('micro_price', 0):,.4f}"),
        ("Spread",          f"{m.get('spread', 0):,.2f}"),
        ("Order Imbalance", f"{m.get('imbalance', 0):.2%}"),
        ("Intraday",        f"{intraday:+.2%}" if intraday is not None else "-"),
        ("Cierre Anterior", f"${closing:,.2f}" if closing > 0 else "-"),
        ("Vs. Cierre",      f"{vs_cierre:+.2%}" if vs_cierre is not None else "-"),
    ]
    st.caption("QUANT ANALYTICS")
    st.dataframe(
        pd.DataFrame(rows, columns=["Métrica", "Valor"]),
        hide_index=True,
        use_container_width=True,
        height=df_height(len(rows)),
    )


_HOURLY_HEIGHT = df_height(7)  # 10hs a 16hs = 7 filas

def render_hourly(hourly_stats):
    rows = []
    for h in range(10, 17):
        d = hourly_stats.get(str(h), {"buy": 0, "sell": 0, "total": 0})
        rows.append({
            "Hora":  f"{h}hs",
            "Total": fmt_money(d.get("total", 0)),
            "Buy":   fmt_money(d.get("buy", 0)),
            "Sell":  fmt_money(d.get("sell", 0)),
        })
    st.caption("HOURLY VOL")
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, height=_HOURLY_HEIGHT)


def render_tape(trades, height=None):
    rows = []
    for t in trades[:30]:
        ts   = t.get("timestamp")
        hora = ts.strftime("%H:%M:%S") if hasattr(ts, 'strftime') else str(ts)[:8]
        rows.append({
            "Hora":   hora,
            "Precio": t.get('price', 0),
            "Size":   t.get('size', 0),
            "Side":   t.get("side", "MID"),
        })
    st.caption("TAPE")
    if not rows:
        st.caption("Sin trades recientes.")
        return
    df = pd.DataFrame(rows)
    styler = (
        df.style
        .map(lambda v: (
            "color: #00cc66; font-weight: bold" if v == "BUY" else
            "color: #ff4444; font-weight: bold" if v == "SELL" else
            "color: #aaa"
        ), subset=["Side"])
        .format({"Precio": "{:,.2f}", "Size": "{:,.0f}"})
    )
    h = height if height is not None else df_height(len(rows), max_h=1200)
    st.dataframe(styler, hide_index=True, use_container_width=True, height=h)


def render_whales(top_trades):
    rows = []
    for t in top_trades[:15]:
        money = t.get("money", (t.get("price", 0) / 100) * t.get("size", 0))
        rows.append({
            "Precio": t.get('price', 0),
            "Monto":  fmt_money(money),
            "Side":   t.get("side", "MID"),
        })
    st.caption("TOP 15 WHALES (CASH)")
    if not rows:
        st.info("Sin datos.")
        return
    df = pd.DataFrame(rows)
    styler = (
        df.style
        .map(lambda v: (
            "color: #00cc66; font-weight: bold" if v == "BUY" else
            "color: #ff4444; font-weight: bold" if v == "SELL" else
            "color: #aaa"
        ), subset=["Side"])
        .format({"Precio": "{:,.2f}"})
    )
    st.dataframe(styler, hide_index=True, use_container_width=True, height=df_height(len(rows), max_h=600))


# ==========================================
# RENDER FUNCTIONS — MERCADO
# ==========================================
def render_mercado_table(snaps, enriched=None):
    if enriched is None:
        enriched = {}
    rows = []
    for snap in snaps:
        ticker     = snap.get("ticker", "")
        m          = snap.get("metrics", {})
        total      = m.get("total_money", 0) or 0
        last_price = m.get("last_price",  0) or 0
        open_price = m.get("open_price",  0) or 0
        closing    = m.get("closing_price", 0) or 0
        vwap       = m.get("vwap",        0) or 0
        if total == 0:
            continue
        intraday  = (last_price / open_price - 1) if open_price > 0 and last_price > 0 else None
        vs_cierre = (last_price / closing - 1) if closing > 0 and last_price > 0 else None

        enc = enriched.get(ticker, {})
        tea = enc.get("TEA")
        dur = enc.get("duration")

        rows.append({
            "Ticker":   short_name(ticker),
            "Last":     last_price if last_price > 0 else None,
            "TEA":      tea,
            "Dur":      dur,
            "Total $":  fmt_money(total),
            "VWAP":     vwap if vwap > 0 else None,
            "Intraday": intraday,
            "1D%":      vs_cierre,
        })
    if not rows:
        st.info("Todos los tickers sin volumen aún.")
        return
    df = pd.DataFrame(rows)

    def pct_color(v):
        if pd.isna(v): return ""
        return "color: #00cc66; font-weight: bold" if v >= 0 else "color: #ff4444; font-weight: bold"

    fmt = {
        "Last":     lambda v: f"{v:,.2f}" if pd.notna(v) else "-",
        "TEA":      lambda v: f"{v:.2%}"  if pd.notna(v) else "-",
        "Dur":      lambda v: f"{v:.2f}"  if pd.notna(v) else "-",
        "VWAP":     lambda v: f"{v:,.2f}" if pd.notna(v) else "-",
        "Intraday": lambda v: f"{v:+.2%}" if pd.notna(v) else "-",
        "1D%":      lambda v: f"{v:+.2%}" if pd.notna(v) else "-",
    }

    styler = (
        df.style
        .map(pct_color, subset=["Intraday", "1D%"])
        .format(fmt)
    )
    st.caption("RESUMEN DE MERCADO")
    st.dataframe(styler, hide_index=True, use_container_width=True, height=df_height(len(df), max_h=900))


def render_tramo_vol(snaps, enriched):
    """Tabla: volumen por tramo (clickable) → detalle de tickers del tramo seleccionado."""
    TRAMOS = [
        ("Corto",  0.0,  0.5,  "≤ 6m"),
        ("Medio",  0.5,  1.5,  "6m–18m"),
        ("Largo",  1.5,  99.0, "> 18m"),
    ]

    buckets = {label: {
        "vol": 0.0, "tea_vol": 0.0, "intra_vol": 0.0, "oned_vol": 0.0,
        "intra_w": 0.0, "oned_w": 0.0,
        "items": [],   # [(snap, enc, total), ...]
    } for label, *_ in TRAMOS}

    for snap in snaps:
        ticker     = snap.get("ticker", "")
        m          = snap.get("metrics", {})
        total      = m.get("total_money", 0) or 0
        if total == 0:
            continue
        enc = enriched.get(ticker, {})
        dur = enc.get("duration")
        tea = enc.get("TEA")
        if dur is None:
            continue

        last_price = m.get("last_price",  0) or 0
        open_price = m.get("open_price",  0) or 0
        closing    = m.get("closing_price", 0) or 0
        intraday   = (last_price / open_price - 1) if open_price > 0 and last_price > 0 else None
        vs_cierre  = (last_price / closing   - 1) if closing   > 0 and last_price > 0 else None

        for label, low, high, _ in TRAMOS:
            if low <= dur < high:
                b = buckets[label]
                b["vol"] += total
                if tea is not None:
                    b["tea_vol"] += tea * total
                if intraday is not None:
                    b["intra_vol"] += intraday * total
                    b["intra_w"]   += total
                if vs_cierre is not None:
                    b["oned_vol"]  += vs_cierre * total
                    b["oned_w"]    += total
                b["items"].append((snap, enc, total))
                break

    total_global = sum(b["vol"] for b in buckets.values())
    if total_global == 0:
        return

    labels_order = [label for label, *_ in TRAMOS]
    rows = []
    for label, _, _, rango in TRAMOS:
        b   = buckets[label]
        vol = b["vol"]
        pct = vol / total_global if total_global > 0 else 0
        tea_p   = b["tea_vol"]  / vol           if vol > 0           else None
        intra_p = b["intra_vol"] / b["intra_w"] if b["intra_w"] > 0  else None
        oned_p  = b["oned_vol"]  / b["oned_w"]  if b["oned_w"]  > 0  else None
        rows.append({
            "Tramo":     f"{label} ({rango})",
            "Vol $":     fmt_money(vol)        if vol > 0            else "—",
            "% Vol":     f"{pct:.0%}"          if vol > 0            else "—",
            "TEA pond.": f"{tea_p:.2%}"        if tea_p   is not None else "—",
            "Intraday":  f"{intra_p:+.2%}"     if intra_p is not None else "—",
            "1D%":       f"{oned_p:+.2%}"      if oned_p  is not None else "—",
        })

    df_summary = pd.DataFrame(rows)

    st.caption("VOLUMEN POR TRAMO · RETORNO PONDERADO")
    ev = st.dataframe(
        df_summary,
        hide_index=True,
        use_container_width=True,
        height=df_height(len(df_summary)),
        on_select="rerun",
        selection_mode="single-row",
        key="tramo_vol_sel",
    )

    # ── Detalle del tramo seleccionado ────────────────────────────────────────
    sel_rows = ev.selection.rows if hasattr(ev, "selection") and ev.selection.rows else []
    if not sel_rows:
        return

    idx       = sel_rows[0]
    label_sel = labels_order[idx]
    items     = sorted(buckets[label_sel]["items"], key=lambda x: x[2], reverse=True)
    if not items:
        return

    st.caption(f"DETALLE — {rows[idx]['Tramo']}")

    det_rows = []
    for snap, enc, total in items:
        m          = snap.get("metrics", {})
        last       = m.get("last_price",    0) or 0
        open_      = m.get("open_price",    0) or 0
        closing    = m.get("closing_price", 0) or 0
        tea        = enc.get("TEA")
        dur        = enc.get("duration")
        intraday   = (last / open_   - 1) if open_   > 0 and last > 0 else None
        vs_cierre  = (last / closing - 1) if closing > 0 and last > 0 else None
        det_rows.append({
            "Ticker":   short_name(snap.get("ticker", "")),
            "TEA":      tea,
            "Dur":      dur,
            "Total $":  fmt_money(total),
            "Intraday": intraday,
            "1D%":      vs_cierre,
        })

    df_det = pd.DataFrame(det_rows)

    def _pct_color(v):
        if pd.isna(v): return ""
        return "color: #00cc66; font-weight: bold" if v >= 0 else "color: #ff4444; font-weight: bold"

    styler_det = (
        df_det.style
        .map(_pct_color, subset=["Intraday", "1D%"])
        .format({
            "TEA":      lambda v: f"{v:.2%}"  if pd.notna(v) else "—",
            "Dur":      lambda v: f"{v:.2f}"  if pd.notna(v) else "—",
            "Intraday": lambda v: f"{v:+.2%}" if pd.notna(v) else "—",
            "1D%":      lambda v: f"{v:+.2%}" if pd.notna(v) else "—",
        })
    )
    st.dataframe(styler_det, hide_index=True, use_container_width=True,
                 height=df_height(len(df_det)))


# ==========================================
# RENDER FUNCTIONS — OPCIONES
# ==========================================
def render_cadena_opciones(docs, spot):
    por_strike = {}
    for d in docs:
        k = d.get('strike')
        t = d.get('tipo')
        if k is None or t is None:
            continue
        if k not in por_strike:
            por_strike[k] = {}
        por_strike[k][t] = d

    if not por_strike:
        st.info("Sin datos de opciones. ¿El motor de opciones está corriendo?")
        return

    def fmt_hl(h, l):
        return f"{h:.1f}/{l:.1f}" if h and h > 0 else "-"

    def intraday(last, open_):
        if last and last > 0 and open_ and open_ > 0:
            return last / open_ - 1
        return None

    rows = []
    for K in sorted(por_strike.keys()):
        c = por_strike[K].get('CALL', {})
        p = por_strike[K].get('PUT',  {})
        c_mid = (c.get('bid', 0) + c.get('offer', 0)) / 2 if c.get('bid', 0) > 0 and c.get('offer', 0) > 0 else c.get('last', 0)
        p_mid = (p.get('bid', 0) + p.get('offer', 0)) / 2 if p.get('bid', 0) > 0 and p.get('offer', 0) > 0 else p.get('last', 0)
        if c_mid == 0 and p_mid == 0:
            continue
        rows.append({
            "C Δ%":    intraday(c.get('last'), c.get('open')),
            "C Vol":   fmt_vol(c.get('ev')),
            "C H/L":   fmt_hl(c.get('high', 0) or 0, c.get('low', 0) or 0),
            "C Delta": c.get('delta'),
            "C IV %":  (c.get('iv') or 0) * 100 if c.get('iv') else None,
            "C Bid":   c.get('bid')   if c.get('bid',   0) > 0 else None,
            "C Offer": c.get('offer') if c.get('offer', 0) > 0 else None,
            "STRIKE":  K,
            "P Bid":   p.get('bid')   if p.get('bid',   0) > 0 else None,
            "P Offer": p.get('offer') if p.get('offer', 0) > 0 else None,
            "P IV %":  (p.get('iv') or 0) * 100 if p.get('iv') else None,
            "P Delta": p.get('delta'),
            "P H/L":   fmt_hl(p.get('high', 0) or 0, p.get('low', 0) or 0),
            "P Vol":   fmt_vol(p.get('ev')),
            "P Δ%":    intraday(p.get('last'), p.get('open')),
        })

    if not rows:
        st.info("Sin precios disponibles.")
        return

    df = pd.DataFrame(rows)

    def pct_color(v):
        if pd.isna(v): return ""
        return "color: #00cc66; font-weight: bold" if v >= 0 else "color: #ff4444; font-weight: bold"

    styler = (
        df.style
        .map(pct_color, subset=["C Δ%", "P Δ%"])
        .map(lambda v: "color: #52946a" if pd.notna(v) else "", subset=["C Bid", "P Bid"])
        .map(lambda v: "color: #b05858" if pd.notna(v) else "", subset=["C Offer", "P Offer"])
        .map(lambda v: "color: #f97316; font-weight: bold", subset=["STRIKE"])
        .format({
            "C Δ%":    lambda v: f"{v:+.1%}" if pd.notna(v) else "-",
            "C Delta": lambda v: f"{v:.3f}"  if pd.notna(v) else "-",
            "C IV %":  lambda v: f"{v:.1f}%" if pd.notna(v) else "-",
            "C Bid":   lambda v: f"{v:.2f}"  if pd.notna(v) else "-",
            "C Offer": lambda v: f"{v:.2f}"  if pd.notna(v) else "-",
            "STRIKE":  "{:,.1f}",
            "P Bid":   lambda v: f"{v:.2f}"  if pd.notna(v) else "-",
            "P Offer": lambda v: f"{v:.2f}"  if pd.notna(v) else "-",
            "P IV %":  lambda v: f"{v:.1f}%" if pd.notna(v) else "-",
            "P Delta": lambda v: f"{v:.3f}"  if pd.notna(v) else "-",
            "P Δ%":    lambda v: f"{v:+.1%}" if pd.notna(v) else "-",
        })
    )

    spot_str = f"${spot:,.2f}" if spot else "N/A"
    st.caption(f"CADENA DE OPCIONES GGAL — SPOT: {spot_str}")
    st.dataframe(styler, hide_index=True, use_container_width=True, height=df_height(len(df), max_h=900))


# ==========================================
# RENDER FUNCTIONS — ESTRATEGIAS DINÁMICAS
# ==========================================
#
# Cada template: (nombre, [(offset, tipo, lado, qty), ...])
# offset relativo al strike central elegido por el usuario.
# lado 'buy'  → ejecución a offer (peor precio para comprador = más conservador)
# lado 'sell' → ejecución a bid
#
def _build_strategy_templates():
    # Cada entrada: (categoria, nombre, patas) — mínimo 5 variantes por categoría
    t = []
    for n in range(1, 7):
        t.append(("Spread Alcista", f"Spread Alcista (Calls) +{n}", [(0,'CALL','buy',1), (+n,'CALL','sell',1)]))
    for n in range(1, 7):
        t.append(("Spread Bajista", f"Spread Bajista (Puts)  -{n}", [(0,'PUT','buy',1), (-n,'PUT','sell',1)]))
    t.append(("Cono / Cuna", "Cono ATM", [(0,'CALL','buy',1), (0,'PUT','buy',1)]))
    for n in range(1, 6):
        t.append(("Cono / Cuna", f"Cuna                   {n}w", [(+n,'CALL','buy',1), (-n,'PUT','buy',1)]))
    for n in range(1, 6):
        t.append(("Ratio", f"Ratio Call 1×2         +{n}", [(0,'CALL','buy',1), (+n,'CALL','sell',2)]))
    for n in range(1, 6):
        t.append(("Ratio", f"Ratio Put  1×2         -{n}", [(0,'PUT','buy',1), (-n,'PUT','sell',2)]))
    for n in range(1, 6):
        t.append(("Backspread", f"Backspread Call        +{n}", [(0,'CALL','sell',1), (+n,'CALL','buy',2)]))
    for n in range(1, 6):
        t.append(("Backspread", f"Backspread Put         -{n}", [(0,'PUT','sell',1), (-n,'PUT','buy',2)]))
    t.append(("Cóndor de Hierro", "Cóndor de Hierro  1|2", [(-2,'PUT','buy',1),(-1,'PUT','sell',1),(+1,'CALL','sell',1),(+2,'CALL','buy',1)]))
    t.append(("Cóndor de Hierro", "Cóndor de Hierro  2|3", [(-3,'PUT','buy',1),(-2,'PUT','sell',1),(+2,'CALL','sell',1),(+3,'CALL','buy',1)]))
    t.append(("Cóndor de Hierro", "Cóndor de Hierro  1|3", [(-3,'PUT','buy',1),(-1,'PUT','sell',1),(+1,'CALL','sell',1),(+3,'CALL','buy',1)]))
    t.append(("Cóndor de Hierro", "Cóndor de Hierro  1|4", [(-4,'PUT','buy',1),(-1,'PUT','sell',1),(+1,'CALL','sell',1),(+4,'CALL','buy',1)]))
    t.append(("Cóndor de Hierro", "Cóndor de Hierro  2|4", [(-4,'PUT','buy',1),(-2,'PUT','sell',1),(+2,'CALL','sell',1),(+4,'CALL','buy',1)]))
    t.append(("Venta de Vol", "Cono Vendido",         [(0,'CALL','sell',1), (0,'PUT','sell',1)]))
    for n in range(1, 5):
        t.append(("Venta de Vol", f"Cuna Vendida           {n}w", [(+n,'CALL','sell',1), (-n,'PUT','sell',1)]))
    return t

STRATEGY_TEMPLATES = _build_strategy_templates()


def render_estrategias_dinamicas(docs, spot, por_strike=None, liquid_strikes=None, center_idx=None, categoria_sel="Todas"):
    # Permite recibir datos pre-computados desde vista_estrategias (evita recalcular)
    if por_strike is None:
        por_strike = {}
        for d in docs:
            k = d.get('strike')
            t = d.get('tipo')
            if k and t:
                if k not in por_strike:
                    por_strike[k] = {}
                por_strike[k][t] = d

    if liquid_strikes is None:
        def is_liquid(d):
            if not d: return False
            return (d.get('bid', 0) or 0) > 0 or (d.get('offer', 0) or 0) > 0
        liquid_strikes = sorted([
            k for k, v in por_strike.items()
            if is_liquid(v.get('CALL')) or is_liquid(v.get('PUT'))
        ])

    if not liquid_strikes or spot <= 0:
        st.info("Sin suficientes datos de mercado para construir estrategias.")
        return

    if center_idx is None:
        center_idx = min(range(len(liquid_strikes)), key=lambda i: abs(liquid_strikes[i] - spot))

    atm_K = liquid_strikes[center_idx]

    def get_px(d, side):
        if not d: return 0
        offer = d.get('offer', 0) or 0
        bid   = d.get('bid',   0) or 0
        last  = d.get('last',  0) or 0
        return (offer if side == 'buy' else bid) if (offer > 0 and bid > 0) else last

    rows = []
    for cat, name, legs in STRATEGY_TEMPLATES:
        if categoria_sel != "Todas" and cat != categoria_sel:
            continue
        neto = d_net = g_net = t_net = 0.0
        valid = True
        used_K = []
        leg_evs = []          # volumen negociado de cada pata (para calcular el cuello de botella)

        for offset, tipo, side, qty in legs:
            idx = center_idx + offset
            if idx < 0 or idx >= len(liquid_strikes):
                valid = False
                break
            K  = liquid_strikes[idx]
            d  = por_strike.get(K, {}).get(tipo)
            px = get_px(d, side)
            if px <= 0:
                valid = False
                break
            m = 1 if side == 'buy' else -1
            neto  += px * qty * m
            d_net += (d.get('delta', 0) or 0) * qty * m
            g_net += (d.get('gamma', 0) or 0) * qty * m
            t_net += (d.get('theta', 0) or 0) * qty * m
            used_K.append(K)
            leg_evs.append((d.get('ev') or 0) / max(qty, 1))   # EV ajustado por ratio

        # Volumen de la pata más restrictiva
        vol_min = min(leg_evs) if valid and leg_evs else None

        rows.append({
            "Estrategia":  name,
            "Strikes":     "/".join(f"{k:,.0f}" for k in sorted(set(used_K))) if valid else "-",
            "Costo/Prima": neto    if valid else None,
            "Vol (pata)":  vol_min if valid else None,
            "Delta":       d_net   if valid else None,
            "Gamma":       g_net   if valid else None,
            "Theta":       t_net   if valid else None,
        })

    df = pd.DataFrame(rows)

    styler = (
        df.style
        .map(lambda v: (
            "color: #ff4444; font-weight: bold" if pd.notna(v) and v > 0 else
            "color: #00cc66; font-weight: bold" if pd.notna(v) else
            "color: #555"
        ), subset=["Costo/Prima"])
        .format({
            "Costo/Prima": lambda v: f"${v:.2f}"   if pd.notna(v) else "Sin Liq",
            "Vol (pata)":  lambda v: fmt_vol(v)     if pd.notna(v) else "-",
            "Delta":       lambda v: f"{v:.3f}"     if pd.notna(v) else "-",
            "Gamma":       lambda v: f"{v:.4f}"     if pd.notna(v) else "-",
            "Theta":       lambda v: f"{v:.2f}"     if pd.notna(v) else "-",
        })
    )
    atm_label = " (ATM)" if center_idx == min(range(len(liquid_strikes)), key=lambda i: abs(liquid_strikes[i] - spot)) else ""
    st.caption(
        f"ESTRATEGIAS — Strike central: {atm_K:,.0f}{atm_label} | Spot: ${spot:,.2f}  |  "
        f"Costo>0 = debit (pagás), Costo<0 = credit (recibís)  |  Vol = EV del leg más restrictivo"
    )
    st.dataframe(styler, hide_index=True, use_container_width=True, height=df_height(len(df), max_h=900))


# ==========================================
# HELPERS — ESTRATEGIAS (datos + gráficos)
# ==========================================

def _calcular_estrategias(por_strike, liquid_strikes, center_idx, spot, categoria_sel="Spread Alcista"):
    """Construye filas de la tabla y la lista de patas resueltas (symbol, K, side, qty, px)."""
    def get_px(d, side):
        if not d: return 0
        offer = d.get('offer', 0) or 0
        bid   = d.get('bid',   0) or 0
        last  = d.get('last',  0) or 0
        return (offer if side == 'buy' else bid) if (offer > 0 and bid > 0) else last

    rows, resolved_legs_list = [], []

    for cat, name, legs in STRATEGY_TEMPLATES:
        if cat != categoria_sel:
            continue
        neto = d_net = g_net = t_net = 0.0
        valid = True
        used_K, leg_evs, resolved_legs = [], [], []

        for offset, tipo, side, qty in legs:
            idx = center_idx + offset
            if idx < 0 or idx >= len(liquid_strikes):
                valid = False; break
            K  = liquid_strikes[idx]
            d  = por_strike.get(K, {}).get(tipo)
            px = get_px(d, side)
            if px <= 0:
                valid = False; break
            m = 1 if side == 'buy' else -1
            neto  += px * qty * m
            d_net += (d.get('delta', 0) or 0) * qty * m
            g_net += (d.get('gamma', 0) or 0) * qty * m
            t_net += (d.get('theta', 0) or 0) * qty * m
            used_K.append(K)
            leg_evs.append((d.get('ev') or 0) / max(qty, 1))
            _iv    = (d.get('iv')    or 0) if d else 0
            _vega  = (d.get('vega')  or 0) if d else 0
            _gamma = (d.get('gamma') or 0) if d else 0
            _dspot = (d.get('spot')  or 0) if d else 0
            _vence = (d.get('vence') or '') if d else ''
            # T exacto: vega/gamma = S²·σ·T  →  T = vega/(gamma·S²·σ)
            if _vega > 0 and _gamma > 0 and _dspot > 0 and _iv > 0:
                _T_leg = _vega / (_gamma * _dspot * _dspot * _iv)
            elif _vence:
                try:
                    _T_leg = max((datetime.strptime(_vence, "%Y%m%d") - datetime.now()).days, 1) / 365.0
                except Exception:
                    _T_leg = None
            else:
                _T_leg = None
            resolved_legs.append({
                'symbol': (d.get('symbol') or '') if d else '',
                'K': K, 'tipo': tipo, 'side': side, 'qty': qty, 'px': px,
                'iv': _iv, 'vence': _vence, 'T': _T_leg,
            })

        rows.append({
            "Estrategia":  name,
            "Strikes":     "/".join(f"{k:,.0f}" for k in sorted(set(used_K))) if valid else "-",
            "Costo/Prima": neto * 100                if valid else None,
            "Vol (pata)":  min(leg_evs) if leg_evs  else None,
            "Delta":       d_net                     if valid else None,
            "Gamma":       g_net                     if valid else None,
            "Theta":       t_net                     if valid else None,
        })
        resolved_legs_list.append(resolved_legs if valid else [])

    return rows, resolved_legs_list


def _chart_historico_estrategia(db_opciones, resolved_legs, costo_actual=None, dias=10):
    """Línea temporal del costo de la estrategia usando Opciones.Data."""
    symbols = [leg['symbol'] for leg in resolved_legs if leg.get('symbol')]
    if not symbols:
        return None

    desde = datetime.utcnow() - timedelta(days=dias)
    docs = list(db_opciones["Data"].find(
        {"symbol": {"$in": symbols}, "timestamp": {"$gte": desde}},
        {"_id": 0, "symbol": 1, "timestamp": 1, "last": 1, "bid": 1, "offer": 1},
    ))
    if not docs:
        return None

    df = pd.DataFrame(docs)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['px'] = df.apply(
        lambda r: (r.get('bid', 0) + r.get('offer', 0)) / 2
        if (r.get('bid') or 0) > 0 and (r.get('offer') or 0) > 0
        else (r.get('last') or 0),
        axis=1,
    )
    df = df[df['px'] > 0]
    if df.empty:
        return None

    df = (df.set_index('timestamp')
           .groupby('symbol')['px']
           .resample('15min').last()
           .reset_index()
           .dropna())

    df_pivot = df.pivot_table(index='timestamp', columns='symbol', values='px', aggfunc='last')
    df_pivot = df_pivot.ffill().dropna()
    if df_pivot.empty:
        return None

    leg_map = {leg['symbol']: leg for leg in resolved_legs}
    costs = []
    for ts, row in df_pivot.iterrows():
        neto = sum(
            px * leg_map[sym]['qty'] * (1 if leg_map[sym]['side'] == 'buy' else -1)
            for sym, px in row.items() if sym in leg_map
        )
        costs.append({'Fecha': ts, 'Costo': round(neto * 100, 2)})

    df_cost = pd.DataFrame(costs)
    if df_cost.empty:
        return None

    # Eje ordinal: solo timestamps con datos reales, sin huecos por feriados/noches
    df_cost = df_cost.sort_values('Fecha').reset_index(drop=True)
    df_cost['segmento'] = (df_cost['Fecha'].diff() > pd.Timedelta(hours=2)).cumsum()
    df_cost['x_ord'] = df_cost['Fecha'].dt.strftime('%d/%m %H:%M')
    sort_order = df_cost['x_ord'].tolist()

    # ~8 etiquetas distribuidas uniformemente
    step = max(1, len(df_cost) // 8)
    tick_values = df_cost['x_ord'].iloc[::step].tolist()

    line = alt.Chart(df_cost).mark_line(color='#4a9eff', strokeWidth=1.5).encode(
        x=alt.X('x_ord:O', sort=sort_order, title=None,
                axis=alt.Axis(values=tick_values, labelAngle=-30)),
        y=alt.Y('Costo:Q', title='Costo ($)'),
        detail='segmento:N',
        tooltip=[alt.Tooltip('x_ord:N', title='Fecha'), alt.Tooltip('Costo:Q', format='$.2f')],
    )
    zero = alt.Chart(pd.DataFrame({'y': [0]})).mark_rule(color='#555', strokeDash=[4, 4]).encode(y='y:Q')

    layers = [line, zero]
    if costo_actual is not None:
        actual_r = alt.Chart(pd.DataFrame({'y': [costo_actual]})).mark_rule(
            color='#ffcc00', strokeDash=[6, 3], strokeWidth=1.5
        ).encode(y='y:Q')
        layers.append(actual_r)

    return alt.layer(*layers).properties(height=400)


def _chart_payoff_estrategia(resolved_legs, spot, neto):
    """Diagrama de payoff al vencimiento. Retorna (chart, lista_breakevens)."""
    import numpy as np
    if not resolved_legs or spot <= 0:
        return None, []

    ggal = np.linspace(spot * 0.65, spot * 1.35, 400)
    intrinseco = np.zeros(len(ggal))
    for leg in resolved_legs:
        m = 1 if leg['side'] == 'buy' else -1
        if leg['tipo'] == 'CALL':
            intrinseco += m * leg['qty'] * np.maximum(ggal - leg['K'], 0)
        else:
            intrinseco += m * leg['qty'] * np.maximum(leg['K'] - ggal, 0)

    pl = intrinseco * 100 - (neto or 0)
    df = pd.DataFrame({'GGAL': ggal, 'PL': pl, 'PL_pos': pl.clip(0), 'PL_neg': pl.clip(None, 0)})

    # Breakevens: cruces de cero por interpolación lineal
    breakevens = []
    sign_changes = np.where(np.diff(np.sign(pl)))[0]
    for i in sign_changes:
        x0, x1, y0, y1 = ggal[i], ggal[i + 1], pl[i], pl[i + 1]
        if y1 != y0:
            be = x0 - y0 * (x1 - x0) / (y1 - y0)
            breakevens.append(round(be, 0))

    base   = alt.Chart(df)
    area_g = base.mark_area(color='#00cc66', opacity=0.55).encode(x='GGAL:Q', y=alt.Y('PL_pos:Q', stack=None))
    area_r = base.mark_area(color='#ff4444', opacity=0.55).encode(x='GGAL:Q', y=alt.Y('PL_neg:Q', stack=None))
    line   = base.mark_line(color='white', strokeWidth=1.2).encode(
        x=alt.X('GGAL:Q', title='GGAL al vencimiento ($)',
                axis=alt.Axis(tickCount=8, format='$,.0f', labelAngle=-30)),
        y=alt.Y('PL:Q', title='P&L ($)', stack=None),
        tooltip=[alt.Tooltip('GGAL:Q', format=',.0f', title='GGAL'), alt.Tooltip('PL:Q', format=',.2f', title='P&L')],
    )
    spot_r = alt.Chart(pd.DataFrame({'x': [spot]})).mark_rule(
        color='#ffcc00', strokeDash=[4, 4], strokeWidth=1.5
    ).encode(x='x:Q')
    zero_r = alt.Chart(pd.DataFrame({'y': [0]})).mark_rule(
        color='#555', strokeDash=[4, 4]
    ).encode(y='y:Q')

    layers = [area_g, area_r, line, spot_r, zero_r]

    if breakevens:
        df_be = pd.DataFrame({'x': breakevens, 'label': [f"BE ${int(b):,}" for b in breakevens]})
        be_r = alt.Chart(df_be).mark_rule(color='#ffffff', strokeDash=[6, 3], strokeWidth=1.2).encode(x='x:Q')
        be_t = alt.Chart(df_be).mark_text(
            color='#ffffff', dy=-8, fontSize=11, fontWeight=600
        ).encode(x='x:Q', text='label:N')
        layers += [be_r, be_t]

    return alt.layer(*layers).properties(height=400), breakevens


# ==========================================
# VISTAS (st.fragment → auto-refresh 1s, sin sleep ni rerun global)
# ==========================================

def vista_libro():
    db = get_db()

    tickers = _cargar_tickers_merv()

    if "selected_ticker" not in st.session_state or st.session_state.selected_ticker not in tickers:
        st.session_state.selected_ticker = tickers[0] if tickers else None

    if not tickers:
        st.warning("Sin tickers en Trading.Curvas.")
        return

    options_short = [short_name(t) for t in tickers]

    # Fila header: selector | vacío | última actualización (alineado sobre Quant)
    col_ticker, col_mid, col_badge = st.columns([1, 1, 1])
    with col_ticker:
        current_idx   = tickers.index(st.session_state.selected_ticker)
        selected_short = st.selectbox(
            "Ticker", options_short,
            index=current_idx,
            label_visibility="collapsed",
        )
        st.session_state.selected_ticker = tickers[options_short.index(selected_short)]

    ticker = st.session_state.selected_ticker
    snap   = db["MarketSnapshot"].find_one({"ticker": ticker})

    if not snap:
        st.warning(f"Sin datos para {ticker}. ¿El motor está corriendo?")
        return

    with col_badge:
        last_update_badge(snap.get("updated_at"))

    book          = snap.get("book", {"bids": [], "offers": []})
    metrics       = snap.get("metrics", {})
    hourly_stats  = snap.get("hourly_stats", {})
    recent_trades = snap.get("recent_trades", [])

    # Tape height: depth (5r) + hourly (7r) + captions/espaciado
    _TAPE_HEIGHT = df_height(5) + _HOURLY_HEIGHT + 80

    # Fila 1: depth+hourly | tape | quant
    col_left, col_center, col_right = st.columns([1, 1, 1])
    with col_left:
        render_depth(book)
        st.write("")
        render_hourly(hourly_stats)
    with col_center:
        render_tape(recent_trades, height=_TAPE_HEIGHT)
    with col_right:
        render_quant(metrics)

    # Fila 2: LAST MINUTES | VOLUME PROFILE
    col_last, col_vp = st.columns([1, 1])

    with col_last:
        trades_sorted = sorted(recent_trades, key=lambda x: x.get("timestamp", datetime.min))
        trade_rows = [
            {
                "Hora":   t["timestamp"] if hasattr(t.get("timestamp"), "strftime") else None,
                "Precio": t.get("price", 0),
                "TEA":    t.get("TEA"),
            }
            for t in trades_sorted
            if t.get("price", 0) > 0 and hasattr(t.get("timestamp"), "strftime")
        ]
        cap_col, _ = st.columns([1, 3])
        with cap_col:
            st.caption("LAST MINUTES")
        if trade_rows:
            chart_df = pd.DataFrame(trade_rows)
            tea_mode  = st.toggle("TEA", key="libro_tea_mode", value=False)

            if tea_mode:
                df_tea = chart_df.dropna(subset=["TEA"])
                if not df_tea.empty:
                    line = (
                        alt.Chart(df_tea)
                        .mark_line(point=True)
                        .encode(
                            x=alt.X("Hora:T", title=None, axis=alt.Axis(format="%H:%M:%S")),
                            y=alt.Y("TEA:Q",   scale=alt.Scale(zero=False), title=None,
                                    axis=alt.Axis(format=".1%")),
                        )
                    )
                    st.altair_chart(
                        line.properties(height=_HOURLY_HEIGHT).interactive(),
                        use_container_width=True,
                    )
                else:
                    st.caption("Sin TEA en los últimos trades.")
            else:
                vwap_val = metrics.get("vwap", 0) or 0
                line = (
                    alt.Chart(chart_df)
                    .mark_line(point=True)
                    .encode(
                        x=alt.X("Hora:T", title=None, axis=alt.Axis(format="%H:%M:%S")),
                        y=alt.Y("Precio:Q", scale=alt.Scale(zero=False), title=None),
                    )
                )
                vwap_rule = (
                    alt.Chart(pd.DataFrame({"vwap": [vwap_val]}))
                    .mark_rule(color="#00cc66", strokeWidth=1.5, strokeDash=[6, 3])
                    .encode(y=alt.Y("vwap:Q"))
                )
                st.altair_chart(
                    alt.layer(line, vwap_rule).properties(height=_HOURLY_HEIGHT).interactive(),
                    use_container_width=True,
                )
        else:
            st.caption("Sin trades recientes.")

    with col_vp:
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        trades_hoy = list(db["TimeSales"].find(
            {"ticker": ticker, "timestamp": {"$gte": today_start}},
            {"price": 1, "money": 1, "_id": 0},
        ))
        st.caption("VOLUME PROFILE")
        if trades_hoy:
            df_vp = pd.DataFrame(trades_hoy)
            df_vp = df_vp[(df_vp["price"] > 0) & (df_vp["money"] > 0)]
            if not df_vp.empty and len(df_vp) >= 2:
                price_range = df_vp["price"].max() - df_vp["price"].min()
                if price_range > 0:
                    raw_tick = price_range / 25
                    magnitude = 10 ** math.floor(math.log10(raw_tick))
                    normalized = raw_tick / magnitude
                    if normalized < 1.5:
                        nice = 1
                    elif normalized < 3.5:
                        nice = 2
                    elif normalized < 7.5:
                        nice = 5
                    else:
                        nice = 10
                    tick = round(nice * magnitude, 10)
                else:
                    tick = 0.01
                df_vp["bucket"] = (df_vp["price"] / tick).round() * tick
                df_vp["bucket"] = df_vp["bucket"].round(10)
                df_agg = (
                    df_vp.groupby("bucket", as_index=False)["money"]
                    .sum()
                    .rename(columns={"bucket": "price_mid"})
                    .sort_values("price_mid")
                )
                vp_chart = (
                    alt.Chart(df_agg)
                    .mark_bar(color="#4c9be8", opacity=0.85)
                    .encode(
                        x=alt.X("price_mid:Q", title=None, axis=alt.Axis(format=",.2f")),
                        y=alt.Y("money:Q",      title=None, axis=alt.Axis(format=",.0f")),
                        tooltip=[
                            alt.Tooltip("price_mid:Q", title="Precio",  format=",.2f"),
                            alt.Tooltip("money:Q",      title="Money",   format=",.0f"),
                        ],
                    )
                    .properties(height=_HOURLY_HEIGHT)
                    .interactive()
                )
                st.altair_chart(vp_chart, use_container_width=True)
            else:
                st.caption("Pocos datos para graficar.")
        else:
            st.caption("Sin trades hoy.")


@st.fragment(run_every=30)
def _tab_opciones_mercado():
    """Solo este fragment se refresca cada 30s."""
    db_op    = get_db_opciones()
    meta_col = get_meta_col()

    docs = list(db_op["OptionsSnapshot"].find({}))
    spot = next((d.get("spot", 0) for d in docs if d.get("spot", 0) > 0), 0) if docs else 0

    vr_doc   = meta_col.find_one({"type": "vr_ggal"})
    vr_local = vr_doc.get("vr_local", 0) if vr_doc else 0
    vr_adr   = vr_doc.get("vr_adr",   0) if vr_doc else 0

    cfg_doc = meta_col.find_one({"type": "config"})
    tasa_actual = cfg_doc.get("tasa", 0.242) if cfg_doc else 0.242
    if "tasa_display" not in st.session_state:
        st.session_state["tasa_display"] = tasa_actual

    ultimo_ts = max((d.get("updated_at") for d in docs if d.get("updated_at")), default=None) if docs else None
    ts_str = (ultimo_ts - timedelta(hours=3)).strftime("%H:%M:%S") if ultimo_ts else "—"

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("SPOT", f"${spot:,.2f}" if spot else "—")
    c2.metric("VR GGAL (40r)", f"{vr_local:.1%}" if vr_local else "—")
    c3.metric("ADR", f"{vr_adr:.1%}" if vr_adr else "—")
    with c4:
        nueva_tasa = st.number_input(
            "Tasa libre de riesgo",
            min_value=0.0, max_value=3.0,
            value=st.session_state["tasa_display"],
            step=0.005, format="%.3f",
            key="tasa_input",
            help="Cambiá el valor y el motor lo aplicará en ~60s",
        )
        if abs(nueva_tasa - st.session_state["tasa_display"]) > 1e-6:
            meta_col.update_one({"type": "config"}, {"$set": {"tasa": nueva_tasa}}, upsert=True)
            st.session_state["tasa_display"] = nueva_tasa
            st.toast(f"Tasa actualizada a {nueva_tasa:.3f}", icon="✅")
    c5.metric("Última act.", ts_str)

    st.divider()

    if not docs:
        st.warning("Sin datos de opciones. ¿El motor de opciones está corriendo?")
    else:
        render_cadena_opciones(docs, spot)

        smile_rows = {}
        for d in docs:
            k = d.get("strike"); t = d.get("tipo"); iv = d.get("iv")
            if k and t and iv and iv > 0:
                if k not in smile_rows:
                    smile_rows[k] = {}
                smile_rows[k][t] = round(iv * 100, 2)
        if smile_rows:
            smile_df = (
                pd.DataFrame.from_dict(smile_rows, orient="index")
                .rename(columns={"CALL": "CALL IV%", "PUT": "PUT IV%"})
                .sort_index()
            )
            st.caption("VOLATILITY SMILE — IV% por strike")
            st.line_chart(smile_df, use_container_width=True)


def vista_opciones():
    db_op = get_db_opciones()

    st.markdown("## ACAQuant | Opciones")
    tab_merc, tab_est, tab_vol = st.tabs(["Mercado", "Estrategias", "Volúmenes"])

    # ── Tab Mercado — auto-refresh 30s ────────────────────────────────────
    with tab_merc:
        _tab_opciones_mercado()

    # ── Tab Estrategias ───────────────────────────────────────────────────
    with tab_est:
        _proj = {"_id": 0, "symbol": 1, "strike": 1, "tipo": 1, "bid": 1, "offer": 1,
                 "last": 1, "ev": 1, "delta": 1, "gamma": 1, "theta": 1,
                 "iv": 1, "spot": 1, "updated_at": 1}
        docs_e = list(db_op["OptionsSnapshot"].find({}, _proj))
        spot_e = next((d.get("spot", 0) for d in docs_e if d.get("spot", 0) > 0), 0) if docs_e else 0

        if not docs_e:
            st.warning("Sin datos de opciones. ¿El motor de opciones está corriendo?")
        else:
            por_strike = {}
            for d in docs_e:
                k = d.get('strike'); t = d.get('tipo')
                if k and t:
                    if k not in por_strike:
                        por_strike[k] = {}
                    por_strike[k][t] = d

            def is_liquid(d):
                if not d: return False
                return (d.get('bid', 0) or 0) > 0 or (d.get('offer', 0) or 0) > 0

            liquid_strikes = sorted([
                k for k, v in por_strike.items()
                if is_liquid(v.get('CALL')) or is_liquid(v.get('PUT'))
            ])

            if not liquid_strikes:
                st.info("Sin strikes con liquidez aún.")
            else:
                atm_idx_default = min(range(len(liquid_strikes)), key=lambda i: abs(liquid_strikes[i] - spot_e))
                atm_K_default   = liquid_strikes[atm_idx_default]

                ultimo_ts_e = max((d.get("updated_at") for d in docs_e if d.get("updated_at")), default=None)
                ts_str_e = (ultimo_ts_e - timedelta(hours=3)).strftime("%H:%M:%S") if ultimo_ts_e else "—"

                categorias_ordenadas = ["Spread Alcista", "Spread Bajista", "Cono / Cuna",
                                        "Ratio", "Backspread", "Cóndor de Hierro", "Venta de Vol"]
                # Solo mostrar categorías que tienen templates definidos
                cats_disponibles = [c for c in categorias_ordenadas
                                    if any(cat == c for cat, _, _ in STRATEGY_TEMPLATES)]

                col_strike, col_cat, col_ts = st.columns([3, 3, 2])
                with col_strike:
                    strike_sel = st.selectbox(
                        "Strike central",
                        options=liquid_strikes,
                        index=atm_idx_default,
                        format_func=lambda k: f"{k:,.0f}{'  ← ATM' if k == atm_K_default else ''}",
                        key="estrategias_strike",
                    )
                with col_cat:
                    categoria_sel = st.selectbox(
                        "Tipo de estrategia",
                        options=cats_disponibles,
                        index=0,
                        key="estrategias_cat",
                    )
                with col_ts:
                    st.metric("Última act.", ts_str_e)

                center_idx = liquid_strikes.index(strike_sel)
                st.divider()

                rows, resolved_legs_list = _calcular_estrategias(
                    por_strike, liquid_strikes, center_idx, spot_e, categoria_sel
                )

                df_est = pd.DataFrame(rows)
                atm_label = " (ATM)" if center_idx == atm_idx_default else ""
                st.caption(
                    f"Strike central: {strike_sel:,.0f}{atm_label} | Spot: ${spot_e:,.2f}  |  "
                    f"Costo>0 = debit (pagás), Costo<0 = credit (recibís)"
                )
                styler = (
                    df_est.style
                    .map(lambda v: (
                        "color: #ff4444; font-weight: bold" if pd.notna(v) and v > 0 else
                        "color: #00cc66; font-weight: bold" if pd.notna(v) else
                        "color: #555"
                    ), subset=["Costo/Prima"])
                    .format({
                        "Costo/Prima": lambda v: f"${v:.2f}"  if pd.notna(v) else "Sin Liq",
                        "Vol (pata)":  lambda v: fmt_vol(v)    if pd.notna(v) else "-",
                        "Delta":       lambda v: f"{v:.3f}"    if pd.notna(v) else "-",
                        "Gamma":       lambda v: f"{v:.4f}"    if pd.notna(v) else "-",
                        "Theta":       lambda v: f"{v:.2f}"    if pd.notna(v) else "-",
                    })
                )
                selection = st.dataframe(
                    styler, hide_index=True, use_container_width=True,
                    height=df_height(len(df_est), max_h=700),
                    on_select="rerun", selection_mode="single-row",
                    key="estrategias_tabla",
                )

                st.divider()

                sel_rows = selection.selection.rows if hasattr(selection, 'selection') else []

                # Default: primera fila con liquidez
                if not sel_rows:
                    default_idx = next(
                        (i for i, r in enumerate(rows) if r.get("Costo/Prima") is not None),
                        0
                    )
                    sel_rows = [default_idx]

                row_idx  = sel_rows[0]
                sel_name = rows[row_idx]["Estrategia"]
                sel_cost = rows[row_idx]["Costo/Prima"]
                sel_legs = resolved_legs_list[row_idx]

                tipo_cost = "DEBIT" if (sel_cost or 0) > 0 else "CREDIT"
                st.markdown(f"### {sel_name}  —  {tipo_cost} ${abs(sel_cost or 0):.2f}")

                # ── Histórico de costo a ancho completo ──────────────────
                chart_h = _chart_historico_estrategia(db_op, sel_legs, costo_actual=sel_cost)
                if chart_h:
                    st.altair_chart(chart_h, use_container_width=True)
                else:
                    st.info("Sin datos históricos suficientes para esta estrategia.")

                st.divider()

                # ── Payoff (izq) + Tabla spread (der) ────────────────────
                chart_p, breakevens = _chart_payoff_estrategia(sel_legs, spot_e, sel_cost)

                _SPREAD_HEIGHT = 560

                col_payoff, col_tabla_spread = st.columns([3, 2])

                with col_payoff:
                    if chart_p:
                        chart_p_tall = chart_p.properties(height=_SPREAD_HEIGHT)
                        st.altair_chart(chart_p_tall, use_container_width=True)
                        be_str = "  Break-even: " + "  /  ".join(f"${int(b):,}" for b in breakevens) if breakevens else ""
                        st.caption(f"Línea amarilla = Spot actual (${spot_e:,.0f}){be_str}")

                with col_tabla_spread:
                    if sel_legs and spot_e > 0:
                        # Tasa y T para pricing teórico
                        _cfg = get_meta_col().find_one({"type": "config"})
                        _r   = (_cfg.get("tasa", 0.242) if _cfg else 0.242)
                        # T: promedio de los T calculados por pata (vega/gamma·S²·σ)
                        _t_vals = [lg['T'] for lg in sel_legs if lg.get('T') and lg['T'] > 0]
                        _T = sum(_t_vals) / len(_t_vals) if _t_vals else None

                        pct_steps = [i * 0.02 for i in range(-7, 8)]
                        spread_rows = []
                        for pct in pct_steps:
                            precio = spot_e * (1 + pct)
                            pl_finish = 0.0
                            pl_teo    = 0.0
                            for leg in sel_legs:
                                m = 1 if leg['side'] == 'buy' else -1
                                if leg['tipo'] == 'CALL':
                                    pl_finish += m * leg['qty'] * max(precio - leg['K'], 0) * 100
                                else:
                                    pl_finish += m * leg['qty'] * max(leg['K'] - precio, 0) * 100
                                if _T and (leg.get('iv') or 0) > 0:
                                    pl_teo += m * leg['qty'] * _bs_price(precio, leg['K'], _T, _r, leg['iv'], leg['tipo']) * 100
                            pl_finish -= (sel_cost or 0)
                            row = {"Precio GGAL": precio, "Var %": pct, "A finish": pl_finish}
                            if _T:
                                row["Teórico"] = pl_teo - (sel_cost or 0)
                            spread_rows.append(row)

                        df_spread = pd.DataFrame(spread_rows)
                        money_cols = ["A finish"] + (["Teórico"] if _T else [])
                        fmt = {"Precio GGAL": "${:,.2f}", "Var %": "{:+.0%}",
                               "A finish": "${:,.2f}"}
                        if _T:
                            fmt["Teórico"] = "${:,.2f}"
                        styler_sp = (
                            df_spread.style
                            .map(lambda v: (
                                "color: #00cc66; font-weight: bold" if isinstance(v, float) and v > 0 else
                                "color: #ff4444; font-weight: bold" if isinstance(v, float) and v < 0 else
                                ""
                            ), subset=money_cols)
                            .format(fmt)
                        )
                        cap = f"±2% por paso | spot ${spot_e:,.2f} | r={_r:.1%}"
                        if _T:
                            cap += f" | T={_T*365:.0f}d (estimado de Greeks)"
                        else:
                            cap += " | Teórico no disponible (Greeks insuficientes)"
                        st.caption(cap)
                        st.dataframe(styler_sp, hide_index=True, use_container_width=True,
                                     height=_SPREAD_HEIGHT)

    # ── Tab Volúmenes ─────────────────────────────────────────────────────
    with tab_vol:
        _render_volumenes_opciones(db_op)


@st.cache_data(ttl=300)
def _fetch_vol_historico():
    """Query única: último ev por (fecha, symbol) → agrupado por (fecha, strike, tipo).
    Usa $year/$month/$dayOfMonth en lugar de $dateToString para máxima compatibilidad.
    Cacheada 5 minutos para no re-query en cada rerun del fragment."""
    fecha_min = datetime.utcnow() - timedelta(days=20)
    # Sin $sort: ev es acumulado → $max da el último valor del día sin ordenar
    pipeline = [
        {"$match": {"timestamp": {"$gte": fecha_min}, "ev": {"$gt": 0},
                    "strike": {"$exists": True}, "tipo": {"$exists": True}}},
        # Paso 1: max(ev) por (año, mes, día, symbol) — equivalente al último valor
        {"$group": {
            "_id": {
                "y": {"$year":       "$timestamp"},
                "m": {"$month":      "$timestamp"},
                "d": {"$dayOfMonth": "$timestamp"},
                "s": "$symbol",
            },
            "ev":     {"$max": "$ev"},
            "strike": {"$first": "$strike"},
            "tipo":   {"$first": "$tipo"},
        }},
        # Paso 2: suma por (año, mes, día, strike, tipo)
        {"$group": {
            "_id": {
                "y": "$_id.y", "m": "$_id.m", "d": "$_id.d",
                "strike": "$strike", "tipo": "$tipo",
            },
            "ev_total": {"$sum": "$ev"},
        }},
    ]
    docs = list(get_mongo_client_read()["Opciones"]["Data"].aggregate(pipeline))
    rows = []
    for d in docs:
        i = d["_id"]
        fecha = f"{i['y']:04d}-{i['m']:02d}-{i['d']:02d}"
        rows.append({
            "fecha":  fecha,
            "Strike": i["strike"],
            "Tipo":   i["tipo"],
            "EV_M":   round(d["ev_total"] / 1_000_000, 3),
        })
    return rows


def _render_volumenes_opciones(_db_op_ignored):
    """Volumen operado (EV) por strike y tipo (CALL/PUT) usando Opciones.Data."""
    rows = _fetch_vol_historico()
    if not rows:
        st.info("Sin datos en Opciones.Data.")
        return

    df_all = pd.DataFrame(rows)
    fechas = sorted(df_all["fecha"].unique())
    if not fechas:
        st.info("Sin fechas disponibles.")
        return

    color_scale = alt.Scale(domain=["CALL", "PUT"], range=["#4a9eff", "#ff4444"])

    # ── Gráfico 1: histórico consolidado — rango de fechas ───────────────
    rango = st.select_slider(
        "Rango de fechas",
        options=fechas,
        value=(fechas[0], fechas[-1]),
        key="vol_hist_rango",
    )
    df_hist_fil = df_all[(df_all["fecha"] >= rango[0]) & (df_all["fecha"] <= rango[1])]
    df_hist = df_hist_fil.groupby(["fecha", "Tipo"], as_index=False)["EV_M"].sum()

    hist_bars = alt.Chart(df_hist).mark_bar().encode(
        x=alt.X("fecha:O", title=None, axis=alt.Axis(labelAngle=-45)),
        y=alt.Y("EV_M:Q",  title="Volumen ($M)", stack=True),
        color=alt.Color("Tipo:N", scale=color_scale,
                        legend=alt.Legend(title=None, orient="top-right")),
        order=alt.Order("Tipo:N", sort="ascending"),
        tooltip=[
            alt.Tooltip("fecha:O",  title="Fecha"),
            alt.Tooltip("Tipo:N",   title="Tipo"),
            alt.Tooltip("EV_M:Q",   title="$M", format=".2f"),
        ],
    ).properties(height=280)
    st.altair_chart(hist_bars, use_container_width=True)

    st.divider()

    # ── Gráfico 2: por strike — día puntual o todas las fechas ───────────
    col_sl, col_tog = st.columns([4, 1])
    with col_tog:
        ver_todo = st.toggle("Todas las fechas", value=False, key="vol_strike_todo")
    with col_sl:
        fecha_sel = st.select_slider(
            "Fecha",
            options=fechas,
            value=fechas[-1],
            key="vol_strike_slider",
            disabled=ver_todo,
        )

    if ver_todo:
        df_dia = df_all.groupby(["Strike", "Tipo"], as_index=False)["EV_M"].sum()
        label_dia = "Todas las fechas"
    else:
        df_dia = df_all[df_all["fecha"] == fecha_sel].copy()
        label_dia = fecha_sel

    total_call  = df_dia[df_dia["Tipo"] == "CALL"]["EV_M"].sum()
    total_put   = df_dia[df_dia["Tipo"] == "PUT"]["EV_M"].sum()
    total_all_d = total_call + total_put

    c1, c2, c3, _ = st.columns([2, 2, 2, 3])
    c1.metric(label_dia, f"${total_all_d:.1f}M")
    c2.metric("CALLs", f"${total_call:.1f}M")
    c3.metric("PUTs",  f"${total_put:.1f}M")

    if df_dia.empty:
        st.info("Sin datos para esta fecha.")
        return

    strikes_order = [f"{k:,.0f}" for k in sorted(df_dia["Strike"].unique())]
    df_dia["Strike_lbl"] = df_dia["Strike"].apply(lambda k: f"{k:,.0f}")

    strike_bars = alt.Chart(df_dia).mark_bar().encode(
        x=alt.X("Strike_lbl:O", sort=strikes_order, title="Strike",
                axis=alt.Axis(labelAngle=-45)),
        y=alt.Y("EV_M:Q", title="Volumen ($M)", stack=True),
        color=alt.Color("Tipo:N", scale=color_scale,
                        legend=alt.Legend(title=None, orient="top-right")),
        order=alt.Order("Tipo:N", sort="ascending"),
        tooltip=[
            alt.Tooltip("Strike_lbl:N", title="Strike"),
            alt.Tooltip("Tipo:N",       title="Tipo"),
            alt.Tooltip("EV_M:Q",       title="$M", format=".3f"),
        ],
    ).properties(height=380)
    st.altair_chart(strike_bars, use_container_width=True)


_MESES_ES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}


def _fmt_plazo(fecha_str):
    from datetime import date as _date
    d = _date.fromisoformat(fecha_str[:10])
    return f"{_MESES_ES[d.month]} {str(d.year)[2:]}"


def _tabla_breakevens(pares):
    """Convierte lista de pares a DataFrame compacto para st.dataframe."""
    rows = []
    for p in pares:
        bkv     = p.get("breakeven_mensual")
        tem     = p.get("tem_lecap")
        tea_cer = p.get("tea_cer")
        par     = p.get("paridad_cer")
        rows.append({
            "Lecap":       p["lecap"],
            "CER":         p["cer"],
            "Plazo":       _fmt_plazo(p["fecha_vencimiento"]),
            "TEM Lecap":   f"{tem * 100:.2f}%" if tem is not None else "—",
            "TEA CER":     f"{tea_cer * 100:.2f}%" if tea_cer is not None else "—",
            "Paridad":     f"{par:.1f}%" if par is not None else "—",
            "BE mensual":  f"{bkv * 100:.2f}%" if bkv is not None else "—",
        })
    return pd.DataFrame(rows)


def _chart_breakevens(pares):
    """Scatter + línea de breakeven mensual por fecha de vencimiento."""
    import altair as alt
    rows = [
        {
            "fecha":      p["fecha_vencimiento"],
            "be_pct":     round(p["breakeven_mensual"] * 100, 4),
            "label":      p["lecap"],
        }
        for p in pares if p.get("breakeven_mensual") is not None
    ]
    if not rows:
        return None

    df = pd.DataFrame(rows)
    df["fecha"] = pd.to_datetime(df["fecha"])

    base = alt.Chart(df).encode(
        x=alt.X("fecha:T", title="Vencimiento", axis=alt.Axis(format="%b %y", labelAngle=-45)),
        y=alt.Y("be_pct:Q", title="BE mensual (%)", scale=alt.Scale(zero=False)),
    )

    line   = base.mark_line(color="#4C9BE8", strokeWidth=1.5)
    points = base.mark_point(color="#4C9BE8", size=80, filled=True)
    labels = base.mark_text(dy=-12, fontSize=10, color="#cccccc").encode(text="label:N")

    return alt.layer(line, points, labels).properties(height=320)


def _resumen_breakevens(pares):
    """Devuelve (df_tramos, bkv_ponderado) para mostrar debajo de la tabla principal."""
    tramos = {"Corto (<90d)": [], "Medio (90-180d)": [], "Largo (>180d)": []}
    pesos_total, bkv_pond = 0.0, 0.0

    for p in pares:
        bkv  = p.get("breakeven_mensual")
        dias = p.get("dias", 0)
        if bkv is None or dias <= 0:
            continue

        # Promedio ponderado global (peso = días)
        bkv_pond    += bkv * dias
        pesos_total += dias

        if dias < 90:
            tramos["Corto (<90d)"].append(bkv)
        elif dias <= 180:
            tramos["Medio (90-180d)"].append(bkv)
        else:
            tramos["Largo (>180d)"].append(bkv)

    rows = []
    for nombre, vals in tramos.items():
        if vals:
            rows.append({
                "Tramo":              nombre,
                "Pares":              len(vals),
                "BE promedio":        f"{(sum(vals)/len(vals))*100:.2f}%",
            })

    df = pd.DataFrame(rows) if rows else pd.DataFrame()
    pond = (bkv_pond / pesos_total) if pesos_total > 0 else None
    return df, pond


@st.cache_data(ttl=60, show_spinner=False)
def _cargar_breakevens_historico():
    db = get_db()
    return list(db["BreakevensHistorico"].find(
        {},
        {"fecha": 1, "pares": 1, "_id": 0}
    ))


def _render_breakevens(db):
    from datetime import date as _date

    tab_live, tab_hist, tab_grafico, tab_simulador = st.tabs(["Tiempo Real", "Histórico", "Gráfico", "Simulador"])

    def _render_pares(pares):
        col_tbl, col_chart = st.columns([4, 5])
        with col_tbl:
            st.dataframe(
                _tabla_breakevens(pares),
                hide_index=True,
                use_container_width=True,
                height=df_height(len(pares)),
            )
            df_tramos, bkv_pond = _resumen_breakevens(pares)
            if bkv_pond is not None:
                st.metric("BE ponderado curva", f"{bkv_pond * 100:.2f}%")
            if not df_tramos.empty:
                st.dataframe(df_tramos, hide_index=True, use_container_width=True)
        with col_chart:
            chart = _chart_breakevens(pares)
            if chart:
                st.altair_chart(chart, use_container_width=True)

    with tab_live:
        doc = db["BreakevensLive"].find_one({"_id": "breakevens"})
        if not doc:
            st.info("Sin datos. ¿El motor de breakevens está corriendo?")
        else:
            updated = doc.get("updated_at")
            if updated:
                last_update_badge(updated)
            pares = doc.get("pares", [])
            if pares:
                _render_pares(pares)
            else:
                st.info("Motor activo pero sin pares calculados aún.")

    with tab_hist:
        fechas = sorted(
            [d["fecha"] for d in db["BreakevensHistorico"].find({}, {"fecha": 1, "_id": 0})],
            reverse=True,
        )
        if not fechas:
            st.info("Sin historial disponible aún.")
        else:
            fecha_sel = st.select_slider("Fecha", options=fechas, key="bkv_fecha_slider")
            doc_hist = db["BreakevensHistorico"].find_one({"fecha": fecha_sel})
            if doc_hist:
                pares = doc_hist.get("pares", [])
                if pares:
                    _render_pares(pares)

    with tab_grafico:
        docs_hist = _cargar_breakevens_historico()
        if not docs_hist:
            st.info("Sin historial disponible aún.")
            return

        # Pares disponibles desde el doc más reciente, ordenados por vencimiento
        doc_ref = max(docs_hist, key=lambda d: d["fecha"])
        pares_ref = doc_ref.get("pares", [])

        def _fmt_fecha(s):
            try:
                return _date.fromisoformat(s[:10]).strftime("%d/%m/%y")
            except Exception:
                return s[:10] if s else ""

        lecap_info = {}
        for p in pares_ref:
            lecap = p.get("lecap")
            if not lecap:
                continue
            lecap_info[lecap] = {
                "fecha_vto": p.get("fecha_vencimiento", ""),
                "dias": p.get("dias", 0),
            }

        # label = "dd/mm/yy · Nd · lecap"
        label_to_lecap = {}
        for lecap, info in sorted(lecap_info.items(), key=lambda kv: kv[1]["fecha_vto"]):
            label = f"{_fmt_fecha(info['fecha_vto'])} · {info['dias']}d · {lecap}"
            label_to_lecap[label] = lecap

        labels_disp = list(label_to_lecap.keys())
        if not labels_disp:
            st.info("Sin plazos disponibles.")
            return

        labels_sel = st.multiselect(
            "Plazos", labels_disp,
            default=labels_disp[:2] if len(labels_disp) >= 2 else labels_disp,
            key="bkv_lecaps_grafico",
        )
        if not labels_sel:
            st.info("Seleccioná al menos un plazo.")
            return

        lecap_to_label = {v: k for k, v in label_to_lecap.items()}
        lecaps_sel = {label_to_lecap[l] for l in labels_sel}

        rows = []
        for doc in docs_hist:
            fecha = doc["fecha"]
            for p in doc.get("pares", []):
                lecap = p.get("lecap")
                bkv = p.get("breakeven_mensual")
                if lecap in lecaps_sel and bkv is not None:
                    rows.append({"fecha": fecha, "plazo": lecap_to_label[lecap], "breakeven": bkv * 100})

        if not rows:
            st.info("Sin datos para los plazos seleccionados.")
            return

        df_bkv = pd.DataFrame(rows).sort_values("fecha")
        fechas_ord = sorted(df_bkv["fecha"].unique())
        chart = (
            alt.Chart(df_bkv)
            .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=40))
            .encode(
                x=alt.X("fecha:O", title="Fecha", sort=fechas_ord,
                        axis=alt.Axis(labelAngle=-45)),
                y=alt.Y("breakeven:Q", title="Breakeven mensual (%)",
                        axis=alt.Axis(format=".2f"),
                        scale=alt.Scale(zero=False)),
                color=alt.Color("plazo:N",
                                scale=alt.Scale(scheme="tableau10"),
                                legend=alt.Legend(orient="top")),
                tooltip=[
                    alt.Tooltip("fecha:O", title="Fecha"),
                    alt.Tooltip("plazo:N", title="Plazo"),
                    alt.Tooltip("breakeven:Q", title="BE mensual (%)", format=".3f"),
                ],
            )
            .properties(height=420)
        )
        st.altair_chart(chart, use_container_width=True)

    with tab_simulador:
        _render_simulador(db)


@st.cache_data(ttl=60, show_spinner=False)
def _cargar_datos_simulador():
    """Carga datos necesarios para el simulador: curvas, CER, días hábiles, últimos precios."""
    db = get_db()
    curvas = list(db["Curvas"].find({}))
    cer_docs = list(db["CER"].find({}, {"fecha": 1, "valor": 1, "_id": 0}))
    dias_habiles = sorted(d["fecha"] for d in db["DiasHabiles"].find({}, {"fecha": 1, "_id": 0}))
    # último precio por ticker desde MarketSnapshot
    snaps = list(db["MarketSnapshot"].find({}, {"ticker": 1, "metrics": 1, "_id": 0}))
    last_price = {}
    for s in snaps:
        t = s.get("ticker")
        p = (s.get("metrics") or {}).get("last_price")
        if t and p:
            last_price[t] = float(p)
    cer_dict = {d["fecha"]: float(d["valor"]) for d in cer_docs}
    return curvas, cer_dict, dias_habiles, last_price


def _render_simulador(db):
    from datetime import date as _date, timedelta

    doc_bkv = db["BreakevensLive"].find_one({"_id": "breakevens"})
    if not doc_bkv or not doc_bkv.get("pares"):
        st.info("Sin pares de breakevens. ¿El motor está corriendo?")
        return

    curvas_list, cer_dict, dias_habiles, last_price = _cargar_datos_simulador()
    if not curvas_list or not cer_dict or not dias_habiles:
        st.info("Faltan datos de referencia (Curvas / CER / DiasHabiles).")
        return

    curvas_por_corto = {d.get("ticker_corto"): d for d in curvas_list if d.get("ticker_corto")}

    # ── Settlement de hoy y CER liquidación ───────────────────────────────────
    def _siguiente_habil(fecha_d):
        s = fecha_d.isoformat()
        for f in dias_habiles:
            if f > s:
                return _date.fromisoformat(f)
        return None

    def _retroceder_n_habiles(fecha_d, n):
        s = fecha_d.isoformat()
        idx = None
        for i, f in enumerate(dias_habiles):
            if f <= s:
                idx = i
        if idx is None or idx < n:
            return None
        return _date.fromisoformat(dias_habiles[idx - n])

    def _cer_en_fecha(fecha_d):
        for i in range(7):
            key = (fecha_d - timedelta(days=i)).isoformat()
            if key in cer_dict:
                return cer_dict[key]
        return None

    def _monto_flujo_cer(f, vn=100):
        amort = float(f.get("amortizacion_pct", 0)) / 100 * vn
        if "cupon_sobre_residual" in f:
            cupon = (float(f.get("cupon_sobre_residual", 0))
                     * float(f.get("residual_previo_pct", 0)) / 100 * vn)
        else:
            cupon = float(f.get("cupon_anual", 0)) * vn
        return amort + cupon

    def _fecha_flujo(f):
        v = f.get("fecha")
        if isinstance(v, str):
            try:
                return _date.fromisoformat(v[:10])
            except Exception:
                return None
        return None

    hoy = _date.today()
    settlement_hoy = _siguiente_habil(hoy)
    if not settlement_hoy:
        st.warning("No se pudo calcular settlement T+1.")
        return
    fecha_cer_liq = _retroceder_n_habiles(settlement_hoy, 10)
    if not fecha_cer_liq:
        st.warning("No se pudo calcular fecha de CER liquidación.")
        return
    cer_liq = _cer_en_fecha(fecha_cer_liq)
    if not cer_liq:
        st.warning("Sin valor de CER para la fecha de liquidación.")
        return

    # ── Input de escenarios ───────────────────────────────────────────────────
    col_info, col_input = st.columns([1, 2])
    with col_info:
        st.markdown(
            f"<div style='font-size:12px;color:#888'>"
            f"Settlement T+1: <b>{settlement_hoy.strftime('%d/%m/%y')}</b> · "
            f"CER liq (<b>{fecha_cer_liq.strftime('%d/%m/%y')}</b>): <b>{cer_liq:,.4f}</b>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with col_input:
        escenarios_str = st.text_input(
            "Escenarios de inflación mensual (%)",
            value="2.0, 2.5, 3.0, 3.2, 3.5",
            key="sim_escenarios",
        )

    try:
        escenarios = [float(x.strip()) / 100 for x in escenarios_str.split(",") if x.strip()]
        escenarios = sorted(set(escenarios))
    except ValueError:
        st.error("Escenarios inválidos. Usá números separados por coma (ej: 2.0, 2.5, 3.0).")
        return

    if not escenarios:
        st.info("Ingresá al menos un escenario.")
        return

    # ── Calcular filas por par ────────────────────────────────────────────────
    filas = []
    for par in doc_bkv["pares"]:
        lecap_corto = par.get("lecap")
        cer_corto = par.get("cer")
        if not lecap_corto or not cer_corto:
            continue
        lecap_doc = curvas_por_corto.get(lecap_corto)
        cer_doc = curvas_por_corto.get(cer_corto)
        if not lecap_doc or not cer_doc:
            continue

        precio_lecap = last_price.get(lecap_doc.get("ticker"))
        precio_cer = last_price.get(cer_doc.get("ticker"))
        flujo_lecap = lecap_doc.get("flujo_vencimiento")
        cer_emision = cer_doc.get("cer_emision")
        if not (precio_lecap and precio_cer and flujo_lecap and cer_emision):
            continue

        ret_lecap = flujo_lecap / precio_lecap - 1

        flujos_cer = cer_doc.get("flujos") or []
        flujos_pendientes = []
        for f in flujos_cer:
            fd = _fecha_flujo(f)
            monto_vn = _monto_flujo_cer(f, float(cer_doc.get("valor_nominal", 100)))
            if fd and fd > settlement_hoy and monto_vn > 0:
                flujos_pendientes.append((fd, monto_vn))

        if not flujos_pendientes:
            continue

        fila = {
            "Par": f"{lecap_corto} · {cer_corto}",
            "Vto": par.get("fecha_vencimiento", "")[:10],
            "Días": par.get("dias", 0),
            "Ret. Lecap": ret_lecap,
            "BE mensual": par.get("breakeven_mensual"),
        }

        for infl in escenarios:
            flujo_cer_est = 0.0
            for fd, monto_vn in flujos_pendientes:
                meses = (fd - fecha_cer_liq).days / 30.0
                cer_k_est = cer_liq * (1 + infl) ** meses
                flujo_cer_est += monto_vn * (cer_k_est / cer_emision)
            ret_cer = flujo_cer_est / precio_cer - 1
            pnl = ret_cer - ret_lecap
            fila[f"{infl * 100:.1f}%"] = pnl

        filas.append(fila)

    if not filas:
        st.info("No hay pares con datos completos para simular.")
        return

    df_sim = pd.DataFrame(filas).sort_values("Días").reset_index(drop=True)

    # ── Render tabla con formato y color ──────────────────────────────────────
    escenarios_cols = [f"{i * 100:.1f}%" for i in escenarios]

    def _fmt_pnl(v):
        if v is None or pd.isna(v):
            return ""
        bps = v * 10000
        signo = "+" if bps >= 0 else ""
        return f"{signo}{bps:,.0f} bps"

    def _color_pnl(v):
        if v is None or pd.isna(v):
            return ""
        if v > 0:
            return "color:#2ea043;font-weight:600"
        if v < 0:
            return "color:#e66767;font-weight:600"
        return ""

    def _fmt_pct(v):
        return f"{v * 100:.2f}%" if v is not None and not pd.isna(v) else ""

    styled = (
        df_sim.style
        .format({"Ret. Lecap": _fmt_pct, "BE mensual": _fmt_pct,
                 **{c: _fmt_pnl for c in escenarios_cols}})
        .map(_color_pnl, subset=escenarios_cols)
    )

    st.dataframe(styled, hide_index=True, use_container_width=True,
                 height=df_height(len(df_sim)))

    st.caption(
        "P&L = Retorno CER − Retorno Lecap bajo el escenario. "
        "Verde: CER le gana a Lecap. Rojo: Lecap le gana a CER. "
        "El BE mensual debería caer entre los dos escenarios donde cambia el signo."
    )


def _render_curva_rendimiento(db):
    import numpy as np
    from datetime import date as _date
    import altair as alt

    curvas_disp = sorted(db["ForwardsHistorico"].distinct("curva"))
    if not curvas_disp:
        st.info("Sin datos históricos de curvas.")
        return

    col1, col2, col3 = st.columns([2, 2, 2])
    with col1:
        curva_sel = st.selectbox("Curva", curvas_disp, key="curva_rend_sel")
    with col2:
        tipo_fit = st.radio("Ajuste", ["Logarítmico", "Polinomial grado 2"], horizontal=True, key="curva_fit_tipo")
    with col3:
        if curva_sel == "tasa_fija":
            metrica = st.radio("Métrica", ["TEA", "TEM"], horizontal=True, key="curva_metrica")
        else:
            metrica = "TEA"

    fechas = sorted([
        d["fecha"] for d in db["ForwardsHistorico"].find(
            {"curva": curva_sel}, {"fecha": 1, "_id": 0}
        )
    ], reverse=True)

    if not fechas:
        st.info("Sin datos para esta curva.")
        return

    fecha_sel = st.select_slider("Fecha", options=fechas, key="curva_fecha_slider")

    doc = db["ForwardsHistorico"].find_one({"curva": curva_sel, "fecha": fecha_sel})
    if not doc:
        return

    tasas = doc.get("tasas", {})
    fecha_ref = _date.fromisoformat(fecha_sel)

    meta_map = {d["ticker_corto"]: d for d in db["Curvas"].find({"curva": curva_sel})}

    puntos = []
    for ticker_corto, tea in tasas.items():
        meta = meta_map.get(ticker_corto)
        if not meta or not meta.get("fecha_vencimiento"):
            continue
        try:
            fecha_vto = _date.fromisoformat(meta["fecha_vencimiento"][:10])
        except Exception:
            continue
        dur = (fecha_vto - fecha_ref).days / 365.0
        if dur <= 0:
            continue
        tea_pct = tea * 100
        tem_pct = ((1 + tea) ** (1 / 12) - 1) * 100
        valor_y = tem_pct if metrica == "TEM" else tea_pct
        puntos.append({"Ticker": ticker_corto, "Duration": round(dur, 4), metrica: round(valor_y, 4)})

    if len(puntos) < 2:
        st.info("Menos de 2 instrumentos con datos para esta curva y fecha.")
        return

    df_pts = pd.DataFrame(puntos).sort_values("Duration")

    x = df_pts["Duration"].values
    y = df_pts[metrica].values

    try:
        if tipo_fit == "Logarítmico":
            coeffs = np.polyfit(np.log(x), y, 1)
            x_fit = np.linspace(x.min(), x.max(), 200)
            y_fit = coeffs[0] * np.log(x_fit) + coeffs[1]
        else:
            coeffs = np.polyfit(x, y, 2)
            x_fit = np.linspace(x.min(), x.max(), 200)
            y_fit = np.polyval(coeffs, x_fit)
        df_fit = pd.DataFrame({"Duration": x_fit, metrica: y_fit})
    except Exception:
        df_fit = None

    # Eje Y dinámico: incluye puntos + línea de fit, padding absoluto
    all_y = list(y)
    if df_fit is not None:
        all_y += list(df_fit[metrica].values)
    rango = max(all_y) - min(all_y)
    padding = rango * 0.10 if rango > 0 else 1.0
    y_min = min(all_y) - padding
    y_max = max(all_y) + padding
    y_fmt = ".1f" if curva_sel == "cer" else ".2f"
    y_title = f"{metrica} (%)"

    y_scale = alt.Scale(domain=[y_min, y_max], zero=False)

    puntos_chart = (
        alt.Chart(df_pts)
        .mark_circle(size=80, color="#00cc66")
        .encode(
            x=alt.X("Duration:Q", title="Duration (años)"),
            y=alt.Y(f"{metrica}:Q", title=y_title, scale=y_scale,
                    axis=alt.Axis(format=y_fmt)),
            tooltip=["Ticker:N",
                     alt.Tooltip("Duration:Q", format=".2f"),
                     alt.Tooltip(f"{metrica}:Q", format=y_fmt)],
        )
    )

    labels_chart = (
        alt.Chart(df_pts)
        .mark_text(dy=-12, fontSize=11, color="#aaa")
        .encode(
            x="Duration:Q",
            y=alt.Y(f"{metrica}:Q", scale=y_scale),
            text="Ticker:N",
        )
    )

    chart = puntos_chart + labels_chart

    if df_fit is not None:
        fit_chart = (
            alt.Chart(df_fit)
            .mark_line(color="#4488ff", strokeWidth=2)
            .encode(
                x="Duration:Q",
                y=alt.Y(f"{metrica}:Q", scale=y_scale),
            )
        )
        chart = chart + fit_chart

    st.altair_chart(chart.properties(height=420), use_container_width=True)


def vista_mercado():
    db = get_db()

    st.markdown("## ACAQuant | Mercado")

    tab_mercado, tab_libro, tab_curvas, tab_breakevens, tab_forwards, tab_retorno, tab_vol = st.tabs(["Mercado", "Libro", "Curvas", "Breakevens", "Forwards", "Retorno Total", "Volúmenes"])

    with tab_mercado:
        @st.fragment(run_every=30)
        def _tab_mercado_live():
            all_snaps = list(db["MarketSnapshot"].find({}))

            # ── Metadata de curvas ────────────────────────────────────────────
            curvas_docs = list(db["Curvas"].find(
                {}, {"ticker": 1, "curva": 1, "fecha_vencimiento": 1, "_id": 0}
            ))
            curvas_meta = {d["ticker"]: d for d in curvas_docs if d.get("ticker")}

            CURVA_LABELS = {"tasa_fija": "Tasa Fija", "cer": "CER"}
            curvas_raw   = sorted({d.get("curva", "") for d in curvas_docs if d.get("curva")})
            opciones     = ["Todas"] + [CURVA_LABELS.get(c, c) for c in curvas_raw]
            raw_map      = {CURVA_LABELS.get(c, c): c for c in curvas_raw}

            # ── Header: filtro (izq) + última actualización (der) ────────────
            col_fil, col_badge = st.columns([1, 3])
            with col_fil:
                filtro_label = st.selectbox(
                    "curva", opciones, key="merc_filtro_curva",
                    label_visibility="collapsed"
                )
            with col_badge:
                if all_snaps:
                    ultimo_ts = max(
                        (s.get("updated_at") for s in all_snaps if s.get("updated_at")),
                        default=None
                    )
                    if ultimo_ts:
                        ts_art = ultimo_ts - timedelta(hours=3)
                        badge = f"Última actualización: {ts_art.strftime('%H:%M:%S')} · auto 30s"
                    else:
                        badge = "auto 30s"
                else:
                    badge = "auto 30s"
                st.markdown(
                    f"<div style='text-align:right;color:gray;font-size:0.8em;padding-top:8px'>"
                    f"{badge}</div>",
                    unsafe_allow_html=True
                )

            # ── Enriquecimiento TEA/Duration desde TimeSales ─────────────────
            curvas_tickers = list(curvas_meta.keys())
            enriched = {}
            if curvas_tickers:
                pipeline = [
                    {"$match": {"ticker": {"$in": curvas_tickers}, "duration": {"$exists": True}}},
                    {"$sort": {"timestamp": -1}},
                    {"$group": {
                        "_id":      "$ticker",
                        "TEA":      {"$first": "$TEA"},
                        "TEM":      {"$first": "$TEM"},
                        "duration": {"$first": "$duration"},
                        "paridad":  {"$first": "$paridad"},
                    }},
                ]
                for r in db["TimeSales"].aggregate(pipeline):
                    enriched[r["_id"]] = r

            # ── Filtro y orden ────────────────────────────────────────────────
            all_snaps.sort(
                key=lambda s: s.get("metrics", {}).get("total_money", 0) or 0,
                reverse=True
            )
            if filtro_label != "Todas":
                curva_sel       = raw_map.get(filtro_label, filtro_label)
                tickers_filtro  = {t for t, d in curvas_meta.items() if d.get("curva") == curva_sel}
                snaps_show      = [s for s in all_snaps if s.get("ticker") in tickers_filtro]
            else:
                snaps_show = all_snaps

            # ── Tablas side by side ───────────────────────────────────────────
            col_main, col_tramo = st.columns([3, 2])
            with col_main:
                render_mercado_table(snaps_show, enriched)
            with col_tramo:
                render_tramo_vol(snaps_show, enriched)

        _tab_mercado_live()

    with tab_libro:
        @st.fragment(run_every=2)
        def _tab_libro_live():
            vista_libro()
        _tab_libro_live()

    with tab_curvas:
        _render_curva_rendimiento(db)

    with tab_breakevens:
        _render_breakevens(db)

    with tab_forwards:
        @st.fragment(run_every=30)
        def _tab_forwards_live():
            _render_forwards(get_db(), key_prefix="fwd_mercado")
        _tab_forwards_live()

    with tab_retorno:
        _render_retorno_total(key_prefix="rt_mercado")

    with tab_vol:
        _render_volumenes()


@st.cache_data(ttl=300, show_spinner=False)
def _get_dolar_oficial():
    """Último valor de Trading.DOLAR (tipo de cambio A3500 desde BCRA)."""
    doc = get_db()["DOLAR"].find_one(sort=[("fecha", -1)])
    return float(doc["valor"]) if doc else None


@st.cache_data(ttl=120, show_spinner=False)
def _get_carteras_df():
    """
    Lee Valuaciones.Carteras + join con Valuaciones.Assets y calcula columna
    'valuación'. Autónomo — no depende de vista_portfolios.
    """
    db_val = get_db_valuaciones()
    docs = list(db_val["Carteras"].find({}, {"_id": 0}))
    if not docs:
        return pd.DataFrame()
    df = pd.DataFrame(docs)
    df["precio_num"] = pd.to_numeric(df["precio"], errors="coerce")
    df["cantidad"]   = pd.to_numeric(df["cantidad"], errors="coerce").fillna(0)

    assets_docs = list(db_val["Assets"].find({}, {"_id": 0, "unidad": 1,
        "CALIFICACION": 1, "CARTERA": 1, "CLASE_ACTIVO": 1,
        "EMISOR": 1, "TICKER": 1, "VENCIMIENTO": 1}))
    if assets_docs:
        df = df.merge(pd.DataFrame(assets_docs), on="unidad", how="left")

    for col in ["TICKER", "EMISOR", "CLASE_ACTIVO", "CARTERA", "CALIFICACION", "VENCIMIENTO"]:
        if col in df.columns:
            df[col] = df[col].fillna("-")

    es_pq_directo = (
        (df.get("CLASE_ACTIVO", pd.Series(dtype=str)) == "OTROS") |
        (df.get("CARTERA", pd.Series(dtype=str)).str.contains("FCI", na=False))
    )
    df["valuación"] = df.apply(
        lambda r: r["cantidad"] * r["precio_num"] if es_pq_directo.loc[r.name]
                  else (r["cantidad"] * r["precio_num"] / 100),
        axis=1,
    )
    return df


@st.cache_data(ttl=60, show_spinner=False)
def _get_valor_mep():
    """Último valor MEP desde Valuaciones.Dolar (sort por timestamp desc)."""
    doc = get_db_valuaciones()["Dolar"].find_one(sort=[("timestamp", -1)])
    if not doc or "mep" not in doc:
        return None
    try:
        return float(doc["mep"])
    except (TypeError, ValueError):
        return None


# ==========================================
# PORTFOLIOS → REPORTES (layout replica informe ejecutivo mensual)
# Datos dummy — a conectar con Mongo tras validación de layout
# ==========================================

_REP_NAVY = "#1F3864"
_REP_NAVY_SOFT = "#2E5596"
_REP_BLUE_BG = "#E7EAF4"

def _rep_section_header(title: str):
    st.markdown(
        f"""<div style="background:{_REP_NAVY};color:#fff;padding:8px 14px;
        border-radius:3px;font-weight:700;letter-spacing:0.3px;margin:18px 0 12px 0;
        text-align:center;font-size:14px;">{title}</div>""",
        unsafe_allow_html=True,
    )


def _rep_kpi(label: str, value: str):
    st.markdown(
        f"""<div style="padding:4px 0">
        <div style="font-size:12px;color:#666;font-weight:600">{label}</div>
        <div style="font-size:18px;color:{_REP_NAVY};font-weight:700">{value}</div>
        </div>""",
        unsafe_allow_html=True,
    )


def _rep_dummy_carteras(cuenta: str, mes: str):
    """Devuelve (ARS, DL, HD, FCI) en ARS para mes dado — dummy."""
    import hashlib
    seed = int(hashlib.md5(f"{cuenta}{mes}".encode()).hexdigest()[:8], 16)
    import random
    rnd = random.Random(seed)
    ars = rnd.uniform(30, 55) * 1e9
    dl  = rnd.uniform(15, 30) * 1e9
    hd  = rnd.uniform(35, 60) * 1e9
    fci = rnd.uniform(10, 30) * 1e9
    return ars, dl, hd, fci


def _render_reporte_ejecutivo():
    import datetime as _dt

    df_carteras = _get_carteras_df()
    if df_carteras.empty:
        st.warning("Sin datos en Valuaciones.Carteras.")
        return
    cuentas_disponibles = sorted(df_carteras["id_cuenta"].dropna().astype(str).unique().tolist())

    col_sel, col_info = st.columns([2, 5])
    with col_sel:
        cuenta_sel = st.selectbox(
            "Cuenta",
            cuentas_disponibles,
            key="rep_cuenta_sel",
        )
    with col_info:
        st.info("📋 Reporte — datos reales conectándose por sección. Secciones pendientes siguen en dummy.")

    fecha_hoy = _dt.date.today()
    fecha_str = fecha_hoy.strftime("%d/%m/%Y")
    mes_actual = fecha_hoy.strftime("%B - %Y").capitalize()
    mes_prev_d = (fecha_hoy.replace(day=1) - _dt.timedelta(days=1))
    mes_prev = mes_prev_d.strftime("%B - %Y").capitalize()

    # ───────────────── 1. RESUMEN EJECUTIVO ─────────────────
    _rep_section_header("RESUMEN EJECUTIVO")

    val_mep = _get_valor_mep() or 0.0
    val_a3500 = _get_dolar_oficial() or 0.0

    # Valuación ARS real = suma de columna 'valuación' para la cuenta
    _df_cta = df_carteras[df_carteras["id_cuenta"].astype(str) == str(cuenta_sel)]
    total_ars = float(_df_cta["valuación"].sum())

    # Breakdown real por cartera — mes actual (groupby CARTERA)
    if "CARTERA" in _df_cta.columns and not _df_cta.empty:
        _grp_cart = _df_cta.groupby("CARTERA")["valuación"].sum()
        ars_mes = float(_grp_cart.get("CARTERA ARS", 0.0))
        dl_mes  = float(_grp_cart.get("CARTERA DL",  0.0))
        hd_mes  = float(_grp_cart.get("CARTERA HD",  0.0))
        fci_mes = float(_grp_cart.get("CARTERA FCI", 0.0))
    else:
        ars_mes = dl_mes = hd_mes = fci_mes = 0.0
    # Mes anterior sigue dummy (requiere histórico — próxima iteración)
    val_a3500_total = total_ars / val_a3500 if val_a3500 else 0.0
    val_usd_total = total_ars / val_mep if val_mep else 0.0

    ars_prev, dl_prev, hd_prev, fci_prev = _rep_dummy_carteras(str(cuenta_sel), mes_prev)

    c1, c2, c3, c4 = st.columns([1.2, 1, 1, 1])
    with c1:
        _rep_kpi("Informe al", fecha_str)
        _rep_kpi("Valor MEP", f"{val_mep:,.2f}")
        _rep_kpi("Valor A3500", f"{val_a3500:,.2f}")
    with c2:
        _rep_kpi("Valuación ARS", f"{total_ars:,.0f}")
    with c3:
        _rep_kpi("Valuación A3500", f"{val_a3500_total:,.0f}")
    with c4:
        _rep_kpi("Valuación USD MEP", f"{val_usd_total:,.0f}")

    col_donut, col_tablas = st.columns([1, 1])

    with col_donut:
        import pandas as _pd
        donut_df = _pd.DataFrame({
            "Cartera": ["Cartera ARS", "Cartera DL", "Cartera HD", "Cartera FCI"],
            "Monto": [ars_mes, dl_mes, hd_mes, fci_mes],
        })
        donut_df = donut_df[donut_df["Monto"] > 0].reset_index(drop=True)
        _s = donut_df["Monto"].sum()
        donut_df["pct"] = (donut_df["Monto"] / _s) if _s else 0.0
        donut_df["label"] = donut_df["pct"].map(lambda v: f"{v:.1%}")
        _palette = {
            "Cartera ARS": "#4472C4", "Cartera DL": "#5B9BD5",
            "Cartera HD":  "#8FAADC", "Cartera FCI": "#B4C7E7",
        }
        _domain = donut_df["Cartera"].tolist()
        _range = [_palette[c] for c in _domain]
        arc = alt.Chart(donut_df).mark_arc(innerRadius=60, outerRadius=110).encode(
            theta=alt.Theta("Monto:Q"),
            color=alt.Color(
                "Cartera:N",
                scale=alt.Scale(domain=_domain, range=_range),
                legend=alt.Legend(title=None, orient="right"),
            ),
            tooltip=["Cartera:N", alt.Tooltip("Monto:Q", format=",.0f"),
                     alt.Tooltip("pct:Q", format=".1%")],
        )
        labels = alt.Chart(donut_df).mark_text(radius=85, size=11, color="white", fontWeight="bold").encode(
            theta=alt.Theta("Monto:Q", stack=True),
            text=alt.Text("label:N"),
        )
        st.altair_chart(
            (arc + labels).properties(
                title=alt.TitleParams(mes_actual.upper(), anchor="middle", fontSize=14),
                height=280,
            ),
            use_container_width=True,
        )

    with col_tablas:
        def _tabla_html(titulo, ars, dl, hd, fci):
            total = ars + dl + hd + fci
            total_dolar = dl + hd
            total_pesos = ars + fci
            def _p(v):
                return (v / total) if total else 0.0
            rows = [
                ("Cartera ARS", ars, _p(ars)),
                ("Cartera DL",  dl,  _p(dl)),
                ("Cartera HD",  hd,  _p(hd)),
                ("Cartera FCI", fci, _p(fci)),
                ("Total Dolarizado", total_dolar, _p(total_dolar)),
                ("Total Pesos",      total_pesos, _p(total_pesos)),
            ]
            rows = [r for r in rows if r[1] > 0]
            if not rows:
                return ""
            tr_rows = "".join(
                f"<tr><td style='padding:4px 8px;border-bottom:1px solid #eee'>{label}</td>"
                f"<td style='padding:4px 8px;border-bottom:1px solid #eee;text-align:right'>{monto:,.0f}</td>"
                f"<td style='padding:4px 8px;border-bottom:1px solid #eee;text-align:right'>{pct:.1%}</td></tr>"
                for label, monto, pct in rows
            )
            return (
                f"<table style='width:100%;border-collapse:collapse;font-size:13px;margin-bottom:14px'>"
                f"<thead><tr style='background:{_REP_NAVY};color:#fff'>"
                f"<th style='padding:6px 8px;text-align:left;font-style:italic'>{titulo}</th>"
                f"<th style='padding:6px 8px;text-align:right'>Monto ARS</th>"
                f"<th style='padding:6px 8px;text-align:right'>Ponderación</th>"
                f"</tr></thead><tbody>{tr_rows}</tbody></table>"
            )

        html_tablas = (
            _tabla_html(mes_actual, ars_mes, dl_mes, hd_mes, fci_mes)
            + _tabla_html(mes_prev, ars_prev, dl_prev, hd_prev, fci_prev)
        )
        st.markdown(
            f"<div style='margin-top:-360px'>{html_tablas}</div>",
            unsafe_allow_html=True,
        )

    # ───────────────── 2. CARTERAS vs BENCHMARKS ─────────────────
    _rep_section_header("Detalle de las carteras vs benchmarks")

    import pandas as _pd
    meses_serie = ["may-25","jun-25","jul-25","ago-25","sept-25","oct-25","nov-25","dic-25","ene-26","feb-26","mar-26"]

    def _serie(seed_key, start=0.0, step_mu=0.03, step_sig=0.02):
        import random as _r
        rnd = _r.Random(hash(f"{cuenta_sel}{seed_key}") & 0xffffffff)
        cur = start
        out = [cur]
        for _ in range(len(meses_serie) - 1):
            cur += rnd.gauss(step_mu, step_sig)
            out.append(cur)
        return out

    serie_total_ars = _serie("total_ars", step_mu=0.032)
    serie_pesos     = _serie("pesos",     step_mu=0.035)
    serie_usd_cart  = _serie("usd_cart",  step_mu=0.013, step_sig=0.015)
    serie_total_usd = _serie("total_usd", step_mu=0.018, step_sig=0.025)
    serie_badlar    = _serie("badlar",    step_mu=0.025, step_sig=0.005)
    serie_infl      = _serie("infl",      step_mu=0.024, step_sig=0.004)
    serie_a3500     = _serie("a3500",     step_mu=0.015, step_sig=0.03)

    def _chart_multi(series_dict, title, colors):
        rows = []
        for nombre, serie in series_dict.items():
            for m, v in zip(meses_serie, serie):
                rows.append({"Mes": m, "Serie": nombre, "Valor": v})
        dfc = _pd.DataFrame(rows)
        line = alt.Chart(dfc).mark_line(point=True).encode(
            x=alt.X("Mes:N", sort=meses_serie, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("Valor:Q", axis=alt.Axis(format=".0%")),
            color=alt.Color("Serie:N",
                            scale=alt.Scale(domain=list(series_dict.keys()), range=colors),
                            legend=alt.Legend(title=None, orient="top")),
            tooltip=["Mes:N", "Serie:N", alt.Tooltip("Valor:Q", format=".2%")],
        ).properties(title=title, height=240)
        return line

    g1, g2 = st.columns(2)
    with g1:
        st.altair_chart(_chart_multi({
            "Badlar": serie_badlar, "Inflacion": serie_infl,
            "Cartera Total ACA BIO": serie_total_ars, "A3500": serie_a3500,
        }, "Cartera Total BIO en ARS vs Benchmarks",
        colors=["#9BB8E0", "#A9D18E", "#1F3864", "#E4A9D4"]), use_container_width=True)
    with g2:
        st.altair_chart(_chart_multi({
            "Cartera Total ACA BIO": serie_total_usd,
        }, "Cartera Total BIO en USD",
        colors=["#1F3864"]), use_container_width=True)

    g3, g4 = st.columns(2)
    with g3:
        st.altair_chart(_chart_multi({
            "Badlar": serie_badlar, "Inflacion": serie_infl,
            "Cartera ARS ACA BIO": serie_pesos,
        }, "Cartera Pesos BIO vs Benchmarks",
        colors=["#9BB8E0", "#A9D18E", "#2E75B6"]), use_container_width=True)
    with g4:
        st.altair_chart(_chart_multi({
            "Cartera USD ACA BIO": serie_usd_cart,
        }, "Cartera USD/DL BIO",
        colors=["#1F3864"]), use_container_width=True)

    # ───────────────── 3. VARIACIONES DEL MES ─────────────────
    _rep_section_header("Variaciones del mes")

    meses_tabla = ["jul-25","ago-25","sept-25","oct-25","nov-25","dic-25","ene-26","feb-26","mar-26","abr-26","may-26","jun-26"]
    def _acum(seed_key, step=0.03, sig=0.02):
        import random as _r
        rnd = _r.Random(hash(f"{cuenta_sel}{seed_key}v2") & 0xffffffff)
        cur = 0
        out = []
        for i in range(9):
            cur += rnd.gauss(step, sig)
            out.append(cur)
        out.extend([None, None, None])
        return out

    tabla_rows = _pd.DataFrame({
        "Mes": meses_tabla,
        "Cart. Total en USD": _acum("tu", 0.015, 0.025),
        "Inflacion":          _acum("inf", 0.024, 0.004),
        "A3500":               _acum("a3", 0.015, 0.03),
        "Badlar":             _acum("bl", 0.025, 0.005),
        "Cart. Pesos":        _acum("cp", 0.035, 0.015),
        "Cart. USD":          _acum("cu", 0.013, 0.015),
    })

    def _pct(v):
        return f"{v*100:,.2f}%" if v is not None else ""

    tabla_disp = tabla_rows.copy()
    for c in tabla_disp.columns:
        if c != "Mes":
            tabla_disp[c] = tabla_disp[c].map(_pct)

    c_tbl, c_var = st.columns([3, 1])
    with c_tbl:
        st.dataframe(tabla_disp, hide_index=True, use_container_width=True,
                     height=df_height(len(tabla_disp), max_h=500))
    with c_var:
        st.markdown(f"<div style='background:{_REP_NAVY};color:#fff;text-align:center;"
                    f"padding:6px;font-weight:700;border-radius:3px'>Variaciones del mes</div>",
                    unsafe_allow_html=True)
        import random as _r
        rnd = _r.Random(hash(f"{cuenta_sel}var") & 0xffffffff)
        var_rows = [
            ("Cart. Total $",      rnd.uniform(0.005, 0.035)),
            ("Cartera Total U$S",  rnd.uniform(0.01, 0.05)),
            ("Cart. Pesos",        rnd.uniform(0.01, 0.05)),
            ("Cart. USD",          rnd.uniform(0.005, 0.025)),
            ("Inflacion",          rnd.uniform(0.015, 0.03)),
            ("Badlar",             rnd.uniform(0.015, 0.03)),
            ("A3500",              rnd.uniform(-0.03, 0.03)),
        ]
        for label, v in var_rows:
            color = "#00aa55" if v >= 0 else "#d14343"
            st.markdown(
                f"<div style='display:flex;justify-content:space-between;"
                f"padding:4px 10px;border-bottom:1px solid #eee'>"
                f"<span>{label}</span><span style='color:{color};font-weight:700'>{v*100:,.1f}%</span></div>",
                unsafe_allow_html=True,
            )

    # ───────────────── 4-7. DETALLE DE ACTIVOS ─────────────────
    def _dummy_activos(clase_grupos, monto_total, seed):
        """Genera filas de activos dummy para una cartera."""
        import random as _r
        rnd = _r.Random(hash(f"{cuenta_sel}{seed}") & 0xffffffff)
        rows = []
        emisores = ["TESORO","PROV. CORDOBA","PROV. MENDOZA","YPF","TECO","PAE",
                    "BCO. MACRO","BCRA","BCO. COMAFI","CGC","GENNEIA","ARCOR","IRSA"]
        califs = ["CCC+","AAA","AA+","AA","AA-.ar","A+","BBB+","BBB.ar"]
        total = 0
        tickers_pool = [f"TK{rnd.randint(10,99)}{chr(rnd.randint(65,90))}" for _ in range(30)]
        for clase in clase_grupos:
            n = rnd.randint(3, 6)
            for _ in range(n):
                vn = rnd.uniform(1e8, 5e9)
                px = rnd.uniform(90, 1400)
                monto = vn * px / 100 if clase != "HD" and clase != "DL" else vn * px
                monto = monto * rnd.uniform(0.1, 0.4)
                total += monto
                rows.append({
                    "Ticker": rnd.choice(tickers_pool),
                    "Emisor": rnd.choice(emisores),
                    "Calif.": rnd.choice(califs),
                    "Clase Act.": clase,
                    "Venc.": f"{rnd.randint(1,28)}/{rnd.randint(1,12)}/{rnd.choice([2026,2027,2028,2029,2030])}",
                    "VN": vn,
                    "Px": px,
                    "Monto": monto,
                    "Tasa": rnd.uniform(-0.15, 0.35),
                    "% Share": monto,
                })
        dfa = _pd.DataFrame(rows)
        factor = monto_total / dfa["Monto"].sum()
        dfa["Monto"] *= factor
        dfa["% Share"] = dfa["Monto"] / monto_total
        return dfa

    def _render_tabla_activos(df_act, titulo, total_label, total_valor):
        _rep_section_header(titulo)
        disp = df_act.copy()
        disp["VN"] = disp["VN"].map(lambda v: f"{v:,.0f}")
        disp["Px"] = disp["Px"].map(lambda v: f"{v:,.2f}")
        disp["Monto"] = disp["Monto"].map(lambda v: f"{v:,.0f}")
        disp["Tasa"] = disp["Tasa"].map(lambda v: f"{v*100:,.1f}%")
        disp["% Share"] = disp["% Share"].map(lambda v: f"{v*100:,.0f}%")
        st.dataframe(disp, hide_index=True, use_container_width=True,
                     height=df_height(len(disp), max_h=600))
        st.markdown(
            f"<div style='text-align:right;font-weight:700;color:{_REP_NAVY};padding:4px'>"
            f"{total_label}: {total_valor:,.0f}</div>",
            unsafe_allow_html=True,
        )

    df_ars = _dummy_activos(["CER", "FIJA", "TAMAR"], ars_mes, "pesos")
    _render_tabla_activos(df_ars, "Detalle de Activos - Cartera Pesos", "CARTERA PESOS", ars_mes)

    df_fci = _dummy_activos(["ARS T1", "MM ARS", "MM USD", "HD T1", "RENTA VARIABLE"], fci_mes, "fci")
    _render_tabla_activos(df_fci, "Detalle de Activos - Cartera FCI", "CARTERA FCI", fci_mes)

    df_hd = _dummy_activos(["HD"], hd_mes, "hd")
    _render_tabla_activos(df_hd, "Detalle de Activos - Cartera HD", "CARTERA HD", hd_mes)

    df_dl = _dummy_activos(["DL"], dl_mes, "dl")
    _render_tabla_activos(df_dl, "Detalle de Activos - Cartera DL", "CARTERA DL", dl_mes)

    # ───────────────── 8. MÉTRICAS GENERALES ─────────────────
    _rep_section_header("Metricas Generales")

    mc1, mc2, mc3 = st.columns(3)

    def _breakdown_tabla(titulo, df_src, group_col, total):
        grp = df_src.groupby(group_col)["Monto"].sum().reset_index()
        grp = grp.sort_values("Monto", ascending=False)
        grp["%"] = grp["Monto"] / total
        grp[group_col] = grp[group_col].astype(str)
        disp = grp.copy()
        disp["Monto"] = disp["Monto"].map(lambda v: f"{v:,.0f}")
        disp["%"] = disp["%"].map(lambda v: f"{v:.0%}")
        st.markdown(f"**{titulo}**")
        st.dataframe(disp, hide_index=True, use_container_width=True,
                     height=df_height(len(disp), max_h=400))
        return grp

    with mc1:
        grp_fci = _breakdown_tabla("CARTERA FCI", df_fci, "Clase Act.", fci_mes)
    with mc2:
        grp_ars = _breakdown_tabla("CARTERA ARS", df_ars, "Clase Act.", ars_mes)
    with mc3:
        df_priv = _pd.concat([df_hd, df_dl])
        df_priv_no_tesoro = df_priv[df_priv["Emisor"] != "TESORO"]
        total_priv = df_priv_no_tesoro["Monto"].sum() if not df_priv_no_tesoro.empty else 1
        _breakdown_tabla("CRÉDITOS PRIVADOS MÁS REPRESENTATIVOS", df_priv_no_tesoro,
                         "Emisor", total_priv)

    mc4, mc5 = st.columns(2)
    with mc4:
        def _donut_mini(df_grp, label_col, titulo):
            dfp = df_grp.copy().rename(columns={label_col: "cat"})
            dfp["pct"] = dfp["Monto"] / dfp["Monto"].sum()
            dfp["lbl"] = dfp["pct"].map(lambda v: f"{v:.0%}")
            arc = alt.Chart(dfp).mark_arc(innerRadius=50, outerRadius=95).encode(
                theta="Monto:Q",
                color=alt.Color("cat:N", legend=alt.Legend(title=None, orient="right")),
                tooltip=["cat:N", alt.Tooltip("pct:Q", format=".1%")],
            )
            lbl = alt.Chart(dfp).mark_text(radius=72, color="white", size=10, fontWeight="bold").encode(
                theta=alt.Theta("Monto:Q", stack=True), text="lbl:N",
            )
            st.altair_chart((arc + lbl).properties(
                title=alt.TitleParams(titulo, anchor="middle"), height=240
            ), use_container_width=True)
        _donut_mini(grp_fci, "Clase Act.", "CARTERA FCI")
    with mc5:
        _donut_mini(grp_ars, "Clase Act.", "CARTERA ARS")

    mc6, mc7 = st.columns(2)
    with mc6:
        df_hd_em = df_hd.groupby("Emisor")["Monto"].sum().reset_index().sort_values("Monto", ascending=False)
        df_hd_em["%"] = df_hd_em["Monto"] / hd_mes
        disp_hd = df_hd_em.copy()
        disp_hd["Monto"] = disp_hd["Monto"].map(lambda v: f"{v:,.0f}")
        disp_hd["%"] = disp_hd["%"].map(lambda v: f"{v:.0%}")
        st.markdown("**CARTERA HD**")
        st.dataframe(disp_hd, hide_index=True, use_container_width=True,
                     height=df_height(len(disp_hd), max_h=400))
    with mc7:
        df_dl_em = df_dl.groupby("Emisor")["Monto"].sum().reset_index().sort_values("Monto", ascending=False)
        df_dl_em["%"] = df_dl_em["Monto"] / dl_mes
        disp_dl = df_dl_em.copy()
        disp_dl["Monto"] = disp_dl["Monto"].map(lambda v: f"{v:,.0f}")
        disp_dl["%"] = disp_dl["%"].map(lambda v: f"{v:.0%}")
        st.markdown("**CARTERA DL**")
        st.dataframe(disp_dl, hide_index=True, use_container_width=True,
                     height=df_height(len(disp_dl), max_h=400))


@st.fragment(run_every=30)
def vista_portfolios():
    """
    Muestra el contenido de Valuaciones.Carteras por cuenta (tab por cuenta).
    main_carteras.py (cron en Digital Ocean) actualiza este collection.
    """
    db_val = get_db_valuaciones()

    st.markdown("## ACAQuant | Portfolios")

    docs = list(db_val["Carteras"].find({}, {"_id": 0}))
    if not docs:
        st.warning("Sin datos de carteras. ¿El cron de `main_carteras.py` está corriendo?")
        return

    df = pd.DataFrame(docs)

    # Dólar Oficial automático desde Trading.DOLAR
    dolar = _get_dolar_oficial()

    # Timestamp de actualización + botón manual
    actualizado = df["actualizado"].dropna().replace("", None).dropna()
    col_ts, col_btn = st.columns([6, 1])
    with col_ts:
        if not actualizado.empty:
            dolar_txt = f" | Dólar Oficial: ${dolar:,.2f}" if dolar else ""
            st.caption(f"Última sincronización Aunesa: {actualizado.iloc[0]}{dolar_txt}")
    with col_btn:
        if st.button("↻ Actualizar", key="btn_actualizar_carteras", use_container_width=True):
            with st.spinner("Sincronizando..."):
                try:
                    import sys, os
                    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "Excel"))
                    import main_carteras
                    main_carteras.run()
                    st.toast("Carteras actualizadas", icon="✅")
                    st.rerun()
                except Exception as e:
                    st.toast(f"Error: {e}", icon="❌")

    # Convertir precio y cantidad a numérico
    df["precio_num"] = pd.to_numeric(df["precio"], errors="coerce")
    df["cantidad"]   = pd.to_numeric(df["cantidad"], errors="coerce").fillna(0)

    # ── join con Assets para traer metadata ───────────────────────────────
    assets_docs = list(db_val["Assets"].find({}, {"_id": 0, "unidad": 1,
        "CALIFICACION": 1, "CARTERA": 1, "CLASE_ACTIVO": 1,
        "EMISOR": 1, "TICKER": 1, "VENCIMIENTO": 1}))
    if assets_docs:
        df_assets = pd.DataFrame(assets_docs)
        df = df.merge(df_assets, on="unidad", how="left")

    # Guardar incompletos ANTES del fillna (TICKER null = sin metadata en Assets)
    _check_cols = [c for c in ["TICKER", "EMISOR", "CARTERA", "CLASE_ACTIVO", "CALIFICACION"] if c in df.columns]
    _incompletos_raw = (
        df[df["TICKER"].isnull()][["unidad"] + _check_cols]
        .drop_duplicates(subset=["unidad"])
        .copy()
    ) if "TICKER" in df.columns else pd.DataFrame()

    # Rellenar NaN en columnas de metadata con "-"
    for col in ["TICKER", "EMISOR", "CLASE_ACTIVO", "CARTERA", "CALIFICACION", "VENCIMIENTO"]:
        if col in df.columns:
            df[col] = df[col].fillna("-")

    # ── valuación: P*Q/100 para bonos, P*Q para OTROS y FCI ──────────────
    es_pq_directo = (
        (df.get("CLASE_ACTIVO", pd.Series(dtype=str)) == "OTROS") |
        (df.get("CARTERA", pd.Series(dtype=str)).str.contains("FCI", na=False))
    ) if "CLASE_ACTIVO" in df.columns or "CARTERA" in df.columns else pd.Series(False, index=df.index)
    df["valuación"] = df.apply(
        lambda r: r["cantidad"] * r["precio_num"] if es_pq_directo.loc[r.name]
                  else (r["cantidad"] * r["precio_num"] / 100),
        axis=1
    )

    # ── formatear Vencimiento: solo Año-Mes ───────────────────────────────
    if "VENCIMIENTO" in df.columns:
        df["VENCIMIENTO"] = pd.to_datetime(df["VENCIMIENTO"], errors="coerce").dt.strftime("%Y-%m")

    # ── sub-tabs principales: Carteras (existente) | Reportes (nuevo) ────
    cuentas = sorted(df["id_cuenta"].dropna().unique().tolist())
    sub_carteras, sub_reportes = st.tabs(["Carteras", "Reportes"])

    with sub_reportes:
        _render_reporte_ejecutivo()

    with sub_carteras:
        tabs = st.tabs([str(c) for c in cuentas])

    def _fmt_group(grp_df, label_col):
        grp_df = grp_df.copy()
        grp_df["Valuación USD"] = grp_df["val_raw"].apply(
            lambda v: fmt_money(v / dolar) if (dolar and pd.notna(v)) else "-"
        )
        grp_df["Valuación ARS"] = grp_df["val_raw"].apply(
            lambda v: fmt_money(v) if pd.notna(v) else "-"
        )
        return grp_df[[label_col, "Valuación ARS", "Valuación USD"]]

    for tab, cuenta in zip(tabs, cuentas):
        with tab:
            df_tab = df[df["id_cuenta"] == cuenta].copy()

            # ── filtro cartera ─────────────────────────────────────────
            carteras = sorted(df_tab["CARTERA"].dropna().unique().tolist()) if "CARTERA" in df_tab.columns else []
            col_fil, col_resumen = st.columns([2, 5])
            with col_fil:
                cartera_sel = st.selectbox("Cartera", ["Todas"] + carteras,
                                           key=f"cartera_{cuenta}")
            df_view = df_tab.copy()
            if cartera_sel != "Todas" and "CARTERA" in df_view.columns:
                df_view = df_view[df_view["CARTERA"] == cartera_sel]

            # ── métricas resumen ───────────────────────────────────────
            total_val = df_view["valuación"].sum()
            with col_resumen:
                m1, m2 = st.columns(2)
                m1.metric("Valuación ARS", fmt_money(total_val) if total_val else "N/A")
                m2.metric("Valuación USD", fmt_money(total_val / dolar) if (dolar and total_val) else "N/A")

            st.divider()

            # ── tabla principal ────────────────────────────────────────
            col_order = ["TICKER", "EMISOR", "VENCIMIENTO", "CLASE_ACTIVO", "CARTERA",
                         "CALIFICACION", "cantidad", "precio_num", "valuación"]
            cols_present = [c for c in col_order if c in df_view.columns]
            display = df_view[cols_present].copy()
            display.rename(columns={
                "cantidad":     "VN",
                "precio_num":   "PX",
                "valuación":    "Valuación",
                "TICKER":       "Ticker",
                "EMISOR":       "Emisor",
                "VENCIMIENTO":  "Vencimiento",
                "CLASE_ACTIVO": "Clase",
                "CARTERA":      "Cartera",
                "CALIFICACION": "Calificación",
            }, inplace=True)
            if "Ticker" in display.columns:
                display = display.sort_values("Ticker")

            num_subset = [c for c in ["VN", "Valuación"] if c in display.columns]
            fmt_cols = {
                "VN":        lambda v: f"{v:,.0f}" if pd.notna(v) else "-",
                "PX":        lambda v: f"{v:,.4f}" if pd.notna(v) else "-",
                "Valuación": lambda v: fmt_money(v) if pd.notna(v) else "-",
            }
            styler = (
                display.style
                .map(lambda v: (
                    "color: #00cc66; font-weight: bold" if pd.notna(v) and v > 0 else
                    "color: #ff4444; font-weight: bold" if pd.notna(v) and v < 0 else ""
                ), subset=num_subset)
                .format({k: v for k, v in fmt_cols.items() if k in display.columns})
            )
            st.dataframe(styler, hide_index=True, use_container_width=True,
                         height=df_height(len(display), max_h=900))

            # ── resumen por cartera ────────────────────────────────────
            st.divider()
            if cartera_sel == "Todas" and "CARTERA" in df_view.columns:
                st.caption(f"VALUACIÓN POR CARTERA — Cuenta {cuenta}")
                raw = (
                    df_view.groupby("CARTERA")["valuación"]
                    .sum().reset_index()
                    .rename(columns={"CARTERA": "Cartera", "valuación": "val_raw"})
                    .sort_values("val_raw", ascending=False)
                )
                st.dataframe(_fmt_group(raw, "Cartera"), hide_index=True,
                             use_container_width=True, height=df_height(len(raw)))

            # ── gráficos analíticos ────────────────────────────────────
            st.divider()
            if "CARTERA" in df_view.columns and "CLASE_ACTIVO" in df_view.columns and "EMISOR" in df_view.columns:
                pie_col = "CLASE_ACTIVO" if cartera_sel != "Todas" else "CARTERA"
                pie_label = "Clase" if cartera_sel != "Todas" else "Cartera"
                pie_data = (
                    df_view.groupby(pie_col)["valuación"]
                    .sum().reset_index()
                    .rename(columns={pie_col: pie_label, "valuación": "Valuación"})
                )
                pie_data = pie_data[pie_data["Valuación"] > 0].copy()
                pie_data = pie_data.sort_values(pie_label)
                total_pie = pie_data["Valuación"].sum()
                pie_data["pct"] = pie_data["Valuación"] / total_pie if total_pie else 0
                pie_data["leyenda"] = pie_data.apply(
                    lambda r: f"{r[pie_label]}  {r['pct']:.1%}", axis=1
                )
                domain_leyenda = pie_data["leyenda"].tolist()

                emisor_data = (
                    df_view.groupby("EMISOR")["valuación"]
                    .sum().reset_index()
                    .rename(columns={"EMISOR": "Emisor", "valuación": "Valuación"})
                    .sort_values("Valuación", ascending=False)
                )

                n_cats = len(pie_data)
                arc = alt.Chart(pie_data).mark_arc(innerRadius=60).encode(
                    theta=alt.Theta("Valuación:Q"),
                    color=alt.Color("leyenda:N",
                                    scale=alt.Scale(domain=domain_leyenda, scheme="tableau10"),
                                    legend=alt.Legend(
                                        title=None, orient="bottom",
                                        columns=min(n_cats, 3), labelLimit=180, symbolSize=120,
                                    )),
                    tooltip=[alt.Tooltip(f"{pie_label}:N"),
                             alt.Tooltip("Valuación:Q", format=",.0f"),
                             alt.Tooltip("pct:Q", format=".1%", title="%")],
                )
                torta = arc.properties(
                    title=alt.TitleParams(f"Composición por {pie_label}", anchor="middle"),
                    height=280,
                )
                emisor_top = emisor_data.head(5).copy()
                emisor_top["Valuación"] = emisor_top["Valuación"].apply(
                    lambda v: fmt_money(v) if pd.notna(v) else "-"
                )
                col_torta, col_emisor = st.columns([3, 2])
                with col_torta:
                    st.altair_chart(torta, use_container_width=True)
                with col_emisor:
                    st.caption("TOP STOCK x EMISOR")
                    st.dataframe(emisor_top, hide_index=True, use_container_width=True,
                                 height=df_height(len(emisor_top), max_h=9999))

            if "VENCIMIENTO" in df_view.columns:
                st.divider()
                venc_data = df_view[
                    df_view["VENCIMIENTO"].notna() &
                    (df_view["VENCIMIENTO"] != "") &
                    (~df_view["VENCIMIENTO"].str.upper().isin(["NO APLICA", "NONE"]))
                ].copy()
                if not venc_data.empty:
                    venc_data = (
                        venc_data.groupby("VENCIMIENTO")["valuación"]
                        .sum().reset_index()
                        .rename(columns={"VENCIMIENTO": "Vencimiento", "valuación": "Valuación"})
                        .sort_values("Vencimiento")
                    )
                    venc_data["label"] = venc_data["Valuación"].apply(fmt_money)
                    bars = (
                        alt.Chart(venc_data)
                        .mark_bar()
                        .encode(
                            x=alt.X("Vencimiento:N", sort=None, axis=alt.Axis(labelAngle=-45)),
                            y=alt.Y("Valuación:Q", axis=alt.Axis(format=",.0f"),
                                     scale=alt.Scale(nice=True)),
                            tooltip=["Vencimiento:N", alt.Tooltip("Valuación:Q", format=",.0f")],
                        )
                    )
                    bar_labels = (
                        alt.Chart(venc_data)
                        .mark_text(align="center", baseline="bottom", dy=-4, fontSize=10, fontWeight="bold")
                        .encode(
                            x=alt.X("Vencimiento:N", sort=None),
                            y=alt.Y("Valuación:Q"),
                            text=alt.Text("label:N"),
                        )
                    )
                    st.altair_chart(
                        (bars + bar_labels).properties(title="Valuación por Vencimiento", height=420),
                        use_container_width=True, theme="streamlit",
                    )

            # ── assets incompletos ─────────────────────────────────────
            if not _incompletos_raw.empty:
                df_inc_cuenta = _incompletos_raw[
                    _incompletos_raw.index.isin(df_tab.index)
                ]
                if not df_inc_cuenta.empty:
                    incompletos = df_inc_cuenta.rename(columns={
                        "unidad": "Unidad", "TICKER": "Ticker", "EMISOR": "Emisor",
                        "CARTERA": "Cartera", "CLASE_ACTIVO": "Clase", "CALIFICACION": "Calificación",
                    })
                    with st.expander(f"⚠️ Assets sin metadata completa ({len(incompletos)})", expanded=False):
                        st.caption("Estos instrumentos tienen datos financieros pero faltan campos en Valuaciones.Assets.")
                        st.dataframe(incompletos, hide_index=True, use_container_width=True,
                                     height=df_height(len(incompletos), max_h=400))


# ==========================================
# OPERACIONES — Cash Flow
# ==========================================
@st.cache_resource(ttl=3600)
def get_db_cashflow():
    return get_mongo_client_read()["CashFlow"]


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_movimientos():
    db = get_db_cashflow()
    docs = list(db["Movimientos"].find({}, {"_id": 0, "fecha": 1, "total": 1, "unidad": 1, "informacion": 1, "cuenta": 1}))
    if not docs:
        return pd.DataFrame()
    df = pd.DataFrame(docs)
    df["fecha"] = pd.to_datetime(df["fecha"], format="%d/%m/%Y", errors="coerce")
    df["total"] = pd.to_numeric(df["total"], errors="coerce").fillna(0)
    return df.dropna(subset=["fecha"]).sort_values("fecha")


@st.cache_data(ttl=600, show_spinner=False)
def _cargar_accionistas():
    """Devuelve dict {cuenta: accionista} desde CashFlow.Accionistas."""
    db = get_db_cashflow()
    docs = list(db["Accionistas"].find({}, {"_id": 0, "cuenta": 1, "accionista": 1}))
    return {d["cuenta"]: d["accionista"] for d in docs if "cuenta" in d}


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_contrapartes():
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

    # Join segmento desde CashFlow.Contrapartes
    cp_docs = list(db["Contrapartes"].find({}, {"_id": 0, "contraparte": 1, "segmento": 1}))
    seg_map = {d["contraparte"]: d.get("segmento") or "Sin clasificar" for d in cp_docs}
    df["segmento"] = df["contraparte"].map(seg_map).fillna("Sin clasificar")

    return df


def _render_flujo_chart_y_cards(df_f, monedas_sel, granularity):
    """Renderiza chart por moneda + tarjetas de resumen sobre df_f ya filtrado."""
    COLOR_ARS = "#094293"
    COLOR_USD = "#00cc66"

    if granularity == "Mensual":
        df_f = df_f.copy()
        df_f["_key"] = df_f["fecha"].dt.strftime("%Y-%m")
        df_agg = df_f.groupby(["_key", "unidad"], as_index=False)["total"].sum()
        df_agg = df_agg.sort_values("_key")
        df_agg["label"] = pd.to_datetime(df_agg["_key"] + "-01").dt.strftime("%b %Y")
    else:
        df_f = df_f.copy()
        df_f["_key"] = df_f["fecha"].dt.strftime("%Y-%m-%d")
        df_agg = df_f.groupby(["_key", "unidad"], as_index=False)["total"].sum()
        df_agg = df_agg.sort_values("_key")
        df_agg["label"] = pd.to_datetime(df_agg["_key"]).dt.strftime("%d/%m/%y")

    x_order = list(dict.fromkeys(df_agg["label"].tolist()))
    df_agg = df_agg.drop(columns="_key")

    def make_chart(moneda, color):
        data = df_agg[df_agg["unidad"] == moneda].copy()
        if data.empty:
            return None
        max_abs = data["total"].abs().max()
        if max_abs >= 1e9:
            data["valor"] = data["total"] / 1e9
            y_title = f"Billones {moneda}"
            y_fmt = ",.2f"
        elif max_abs >= 1e6:
            data["valor"] = data["total"] / 1e6
            y_title = f"Millones {moneda}"
            y_fmt = ",.1f"
        elif max_abs >= 1e3:
            data["valor"] = data["total"] / 1e3
            y_title = f"Miles {moneda}"
            y_fmt = ",.1f"
        else:
            data["valor"] = data["total"]
            y_title = moneda
            y_fmt = ",.0f"

        x_enc = alt.X("label:O", sort=x_order, axis=alt.Axis(labelAngle=-45, title=None))
        y_enc = alt.Y("valor:Q", axis=alt.Axis(title=y_title, titleColor=color, format=y_fmt))
        tip = [alt.Tooltip("label:O", title="Fecha"),
               alt.Tooltip("valor:Q", title=y_title, format=y_fmt)]

        bars = (
            alt.Chart(data)
            .mark_bar(color=color, opacity=0.85,
                      cornerRadiusTopLeft=2, cornerRadiusTopRight=2)
            .encode(x=x_enc, y=y_enc, tooltip=tip)
        )

        if granularity != "Mensual":
            return bars.properties(height=220)

        max_val = data["valor"].abs().max()
        umbral = max_val * 0.20
        padding = max_val * 0.04

        data["mid"] = data["valor"] / 2
        data["exterior"] = data["valor"].apply(
            lambda v: v + padding if v >= 0 else v - padding
        )

        grandes = data[data["valor"].abs() >= umbral]
        chicas = data[data["valor"].abs() < umbral]

        txt_inside = (
            alt.Chart(grandes)
            .mark_text(align="center", fontSize=10, fontWeight=600, color="white")
            .encode(x=x_enc, y=alt.Y("mid:Q"), text=alt.Text("valor:Q", format=",.1f"))
        )
        txt_outside = (
            alt.Chart(chicas)
            .mark_text(align="center", fontSize=10, fontWeight=600, color=color)
            .encode(x=x_enc, y=alt.Y("exterior:Q"), text=alt.Text("valor:Q", format=",.1f"))
        )

        return alt.layer(bars, txt_inside, txt_outside).properties(height=260)

    for moneda, color in [("ARS", COLOR_ARS), ("USD", COLOR_USD)]:
        if moneda not in monedas_sel:
            continue
        chart = make_chart(moneda, color)
        if chart:
            st.altair_chart(chart, use_container_width=True)

    tarjeta_cols = st.columns(len(monedas_sel)) if monedas_sel else []
    for i, moneda in enumerate(monedas_sel):
        color = COLOR_ARS if moneda == "ARS" else COLOR_USD
        sub = df_f[df_f["unidad"] == moneda]
        entradas = sub[sub["total"] > 0]["total"].sum()
        salidas = sub[sub["total"] < 0]["total"].sum()
        neto = entradas + salidas
        neto_color = "#00cc66" if neto >= 0 else "#ff4444"
        with tarjeta_cols[i]:
            st.markdown(f"""
<div style="border:1px solid {color};border-radius:8px;padding:14px 18px;margin-top:8px">
  <div style="color:{color};font-weight:700;font-size:13px;letter-spacing:1px;margin-bottom:10px">{moneda}</div>
  <div style="display:flex;gap:24px;flex-wrap:wrap">
    <div>
      <div style="color:#888;font-size:10px;text-transform:uppercase;letter-spacing:.5px">Entradas</div>
      <div style="color:#00cc66;font-size:20px;font-weight:600">{fmt_nom(entradas)}</div>
    </div>
    <div>
      <div style="color:#888;font-size:10px;text-transform:uppercase;letter-spacing:.5px">Salidas</div>
      <div style="color:#ff4444;font-size:20px;font-weight:600">{fmt_nom(abs(salidas))}</div>
    </div>
    <div>
      <div style="color:#888;font-size:10px;text-transform:uppercase;letter-spacing:.5px">Flujo Neto</div>
      <div style="color:{neto_color};font-size:20px;font-weight:600">{("-" if neto < 0 else "+") + fmt_nom(abs(neto))}</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)


_COOP_RE = re.compile(r"\bcoop", re.IGNORECASE)


def _es_cooperativa(cuenta_str):
    return bool(cuenta_str) and bool(_COOP_RE.search(str(cuenta_str)))


def vista_operaciones():
    st.markdown("## ACAQuant | Operaciones")

    tab_cf, tab_cp, tab_analisis_cp, tab_fva = st.tabs(["Cash Flow", "Contrapartes", "Análisis", "Flujo vs AuM"])

    # ── Tab: Cash Flow (sin cambios) ──────────────────────────────────────────
    with tab_cf:
        df = _cargar_movimientos()
        if df.empty:
            st.warning("Sin datos. Ejecutá `main_cashflow.py` para cargar el historial.")
        else:
            min_date = df["fecha"].min().date()
            max_date = df["fecha"].max().date()

            COLOR_ARS = "#094293"
            COLOR_USD = "#00cc66"

            # ── Slider (fila completa) ────────────────────────────────────────
            rango = st.slider(
                "Rango de fechas",
                min_value=min_date,
                max_value=max_date,
                value=(min_date, max_date),
                format="DD/MM/YY",
                key="ops_rango",
            )

            # ── Filtros en una fila ───────────────────────────────────────────
            c_ars, c_usd, c_sep, c_gran = st.columns([1, 1, 3, 3])
            with c_ars:
                show_ars = st.checkbox("ARS", value=True, key="ops_ars")
            with c_usd:
                show_usd = st.checkbox("USD", value=True, key="ops_usd")
            with c_gran:
                granularity = st.radio(
                    "", ["Diario", "Mensual"], horizontal=True,
                    key="ops_gran", label_visibility="collapsed"
                )

            # ── Filtro cuentas ────────────────────────────────────────────────
            acc_map = _cargar_accionistas()   # {cuenta: accionista}

            fa_col, fb_col = st.columns([2, 5])
            with fa_col:
                filtro_acc = st.selectbox(
                    "Cuentas",
                    ["Todas", "Sin accionistas", "Solo accionistas", "Solo cooperativas"],
                    key="ops_filtro_acc",
                    label_visibility="collapsed",
                )

            todas_cuentas = df["cuenta"].dropna().unique().tolist()
            if filtro_acc == "Solo accionistas":
                accionistas_disponibles = sorted({
                    acc_map[c] for c in todas_cuentas if c in acc_map
                })
                opciones = ["Todos"] + accionistas_disponibles
                label_sel = "Accionista"
            elif filtro_acc == "Sin accionistas":
                opciones = ["Todas"] + sorted(c for c in todas_cuentas if c not in acc_map)
                label_sel = "Cuenta"
            elif filtro_acc == "Solo cooperativas":
                cuentas_coop = sorted(
                    c for c in todas_cuentas
                    if c not in acc_map and _es_cooperativa(c)
                )
                opciones = ["Todas"] + cuentas_coop
                label_sel = "Cooperativa"
            else:
                opciones = ["Todas"] + sorted(todas_cuentas)
                label_sel = "Cuenta"

            with fb_col:
                seleccion = st.selectbox(
                    label_sel, opciones, key=f"ops_sel_{filtro_acc}",
                    label_visibility="collapsed",
                )

            # ── Filtrar ───────────────────────────────────────────────────────
            df_f = df[(df["fecha"].dt.date >= rango[0]) & (df["fecha"].dt.date <= rango[1])].copy()
            monedas_sel = (["ARS"] if show_ars else []) + (["USD"] if show_usd else [])
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
                df_f = df_f[df_f["_accionista"].isna() & df_f["cuenta"].apply(_es_cooperativa)].copy()
                if seleccion != "Todas":
                    df_f = df_f[df_f["cuenta"] == seleccion].copy()
            else:
                if seleccion != "Todas":
                    df_f = df_f[df_f["cuenta"] == seleccion].copy()

            if df_f.empty:
                st.info("Sin datos para el rango/moneda seleccionados.")
            else:
                _render_flujo_chart_y_cards(df_f, monedas_sel, granularity)

    # ── Tab: Contrapartes ─────────────────────────────────────────────────────
    with tab_cp:
        df_cp = _cargar_contrapartes()
        if df_cp.empty:
            st.warning("Sin datos en CashFlow.Flujo.")
        else:
            df_cp["_mes"] = df_cp["concertacion"].dt.strftime("%Y-%m")
            df_cp["label"] = df_cp["concertacion"].dt.strftime("%b %Y")

            # ── Filtros: segmento (izq) + moneda (der) en una sola fila ─────────
            segs_disp   = sorted(df_cp["segmento"].dropna().unique().tolist())
            monedas_disp = sorted(df_cp["moneda"].dropna().unique().tolist())

            # Columnas: título_seg | seg×N | spacer | título_mon | mon×N
            n_seg = len(segs_disp)
            n_mon = len(monedas_disp)
            widths = [0.6] + [0.7] * n_seg + [3] + [0.6] + [0.7] * n_mon
            fcols = st.columns(widths)

            with fcols[0]:
                st.caption("SEGMENTO")
            segs_sel = []
            for i, s in enumerate(segs_disp):
                with fcols[1 + i]:
                    if st.checkbox(s, value=True, key=f"cp_seg_{s}"):
                        segs_sel.append(s)

            with fcols[1 + n_seg + 1]:
                st.caption("MONEDA")
            monedas_sel_cp = []
            for i, m in enumerate(monedas_disp):
                with fcols[1 + n_seg + 2 + i]:
                    if st.checkbox(m, value=True, key=f"cp_mon_{m}"):
                        monedas_sel_cp.append(m)

            df_cp = df_cp[df_cp["segmento"].isin(segs_sel)].copy() if segs_sel else df_cp.iloc[0:0]
            df_cp = df_cp[df_cp["moneda"].isin(monedas_sel_cp)].copy() if monedas_sel_cp else df_cp.iloc[0:0]

            if df_cp.empty:
                st.info("Sin datos para las monedas seleccionadas.")
            else:
                labels_all = (
                    df_cp.drop_duplicates("_mes")
                    .sort_values("_mes")["label"]
                    .tolist()
                )

                # ── Selector de rango de fechas ───────────────────────────────
                if len(labels_all) >= 2:
                    desde_lbl, hasta_lbl = st.select_slider(
                        "Período",
                        options=labels_all,
                        value=(labels_all[0], labels_all[-1]),
                        key="cp_rango",
                    )
                else:
                    desde_lbl = hasta_lbl = labels_all[0]

                desde_mes = df_cp.loc[df_cp["label"] == desde_lbl, "_mes"].iloc[0]
                hasta_mes = df_cp.loc[df_cp["label"] == hasta_lbl, "_mes"].iloc[0]
                df_f = df_cp[(df_cp["_mes"] >= desde_mes) & (df_cp["_mes"] <= hasta_mes)].copy()

                # Un gráfico por moneda seleccionada
                COLOR_CP = {"ARS": "#094293", "USD": "#00cc66"}
                for moneda in monedas_sel_cp:
                    df_m = df_f[df_f["moneda"] == moneda].copy()
                    if df_m.empty:
                        continue
                    df_mes = (
                        df_m.groupby(["_mes", "label"], as_index=False)["bruto"]
                        .sum()
                        .sort_values("_mes")
                        .reset_index(drop=True)
                    )
                    df_mes["acumulado"] = df_mes["bruto"].cumsum()
                    mes_order = df_mes["label"].tolist()
                    color = COLOR_CP.get(moneda, "#094293")

                    max_val = df_mes["acumulado"].abs().max()
                    if max_val >= 1e9:
                        df_mes["y"] = df_mes["acumulado"] / 1e9
                        y_title = f"Billones {moneda}"
                        y_fmt   = ",.2f"
                    elif max_val >= 1e6:
                        df_mes["y"] = df_mes["acumulado"] / 1e6
                        y_title = f"Millones {moneda}"
                        y_fmt   = ",.1f"
                    else:
                        df_mes["y"] = df_mes["acumulado"]
                        y_title = moneda
                        y_fmt   = ",.0f"

                    base = alt.Chart(df_mes).encode(
                        x=alt.X("label:O", sort=mes_order, axis=alt.Axis(labelAngle=-45, title=None)),
                    )
                    area = base.mark_area(color=color, opacity=0.12, interpolate="monotone").encode(
                        y=alt.Y("y:Q", axis=alt.Axis(title=y_title, format=y_fmt))
                    )
                    line = base.mark_line(color=color, strokeWidth=2, interpolate="monotone",
                                          point=alt.OverlayMarkDef(size=60, color=color)).encode(
                        y=alt.Y("y:Q"),
                        tooltip=[
                            alt.Tooltip("label:O", title="Mes"),
                            alt.Tooltip("y:Q", format=y_fmt, title=y_title),
                        ]
                    )
                    st.altair_chart(
                        alt.layer(area, line).properties(height=300),
                        use_container_width=True
                    )

                st.divider()

                # Selector de moneda para la tabla (solo si hay más de una)
                if len(monedas_sel_cp) > 1:
                    moneda_tabla = st.radio(
                        "", monedas_sel_cp, horizontal=True,
                        key="cp_mon_tabla", label_visibility="collapsed"
                    )
                else:
                    moneda_tabla = monedas_sel_cp[0]

                df_tabla = df_f[df_f["moneda"] == moneda_tabla]
                total_tabla = df_tabla["bruto"].sum()
                resumen_cp = (
                    df_tabla.groupby("contraparte", as_index=False)["bruto"]
                    .sum()
                    .sort_values("bruto", ascending=False)
                    .reset_index(drop=True)
                )
                resumen_cp["Bruto"] = resumen_cp["bruto"].apply(lambda v: f"{v:,.0f}")
                resumen_cp["%"]     = (resumen_cp["bruto"] / total_tabla * 100).apply(lambda v: f"{v:.1f}%")

                h_cp = 38 + 35 * len(resumen_cp)

                col_izq, col_der = st.columns(2)

                with col_izq:
                    st.markdown(
                        f"<div style='font-size:12px;color:#888'>Contrapartes · {moneda_tabla} · {desde_lbl} → {hasta_lbl}</div>",
                        unsafe_allow_html=True,
                    )
                    ev_cp = st.dataframe(
                        resumen_cp[["contraparte", "Bruto", "%"]],
                        hide_index=True, use_container_width=True,
                        height=h_cp,
                        on_select="rerun",
                        selection_mode="single-row",
                        key="cp_tabla",
                    )

                with col_der:
                    sel_rows = ev_cp.selection.rows if ev_cp.selection.rows else []
                    if not sel_rows:
                        st.markdown(
                            "<div style='font-size:13px;color:#888;padding:8px'>"
                            "Seleccioná una contraparte para ver el detalle.</div>",
                            unsafe_allow_html=True,
                        )
                    else:
                        cp_sel = resumen_cp.iloc[sel_rows[0]]["contraparte"]
                        df_det = df_tabla[df_tabla["contraparte"] == cp_sel].copy()

                        st.markdown(
                            f"<div style='font-size:11px;color:#888;padding:2px 4px 6px'>"
                            f"{cp_sel} · {moneda_tabla}</div>",
                            unsafe_allow_html=True,
                        )

                        # Tabla 1: volumen mensual (más reciente → más antiguo), seleccionable
                        df_mes_det = (
                            df_det.groupby(["_mes", "label"], as_index=False)["bruto"]
                            .sum()
                            .sort_values("_mes", ascending=False)
                            .reset_index(drop=True)
                        )
                        df_mes_det["Bruto"] = df_mes_det["bruto"].apply(lambda v: f"{v:,.0f}")
                        # h_mes: contenido natural pero sin pasar de la mitad
                        h_mes = min(38 + 35 * len(df_mes_det), h_cp // 2)

                        ev_mes = st.dataframe(
                            df_mes_det[["label", "Bruto"]].rename(columns={"label": "Mes"}),
                            hide_index=True, use_container_width=True,
                            height=h_mes,
                            on_select="rerun",
                            selection_mode="multi-row",
                            key="cp_mes_tabla",
                        )

                        # Tabla 2: tipoOperacion filtrada por meses seleccionados (o total)
                        sel_mes_rows = ev_mes.selection.rows if ev_mes.selection.rows else []
                        if sel_mes_rows:
                            meses_sel = df_mes_det.iloc[sel_mes_rows]["_mes"].tolist()
                            df_tipo_src = df_det[df_det["_mes"].isin(meses_sel)]
                            tipo_titulo = ", ".join(df_mes_det.iloc[sel_mes_rows]["label"].tolist())
                        else:
                            df_tipo_src = df_det
                            tipo_titulo = "Total"

                        total_tipo = df_tipo_src["bruto"].sum()
                        df_tipo = (
                            df_tipo_src.groupby("tipoOperacion", as_index=False)["bruto"]
                            .sum()
                            .sort_values("bruto", ascending=False)
                            .reset_index(drop=True)
                        )
                        df_tipo["Bruto"] = df_tipo["bruto"].apply(lambda v: f"{v:,.0f}")
                        df_tipo["%"]     = (df_tipo["bruto"] / total_tipo * 100).apply(lambda v: f"{v:.1f}%") if total_tipo else "—"
                        # h_tipo: ocupa el espacio restante hasta h_cp, ajustado al contenido
                        h_tipo = min(38 + 35 * len(df_tipo), h_cp - h_mes)

                        st.markdown(
                            f"<div style='font-size:10px;color:#888;padding:2px 4px 2px'>Tipo op. · {tipo_titulo}</div>",
                            unsafe_allow_html=True,
                        )
                        st.dataframe(
                            df_tipo[["tipoOperacion", "Bruto", "%"]].rename(columns={"tipoOperacion": "Tipo"}),
                            hide_index=True, use_container_width=True,
                            height=h_tipo,
                        )

    # ── Tab: Análisis contrapartes ────────────────────────────────────────────
    with tab_analisis_cp:
        df_an = _cargar_contrapartes()
        if df_an.empty:
            st.warning("Sin datos en CashFlow.Flujo.")
        else:
            df_an["_mes"] = df_an["concertacion"].dt.strftime("%Y-%m")
            df_an["label"] = df_an["concertacion"].dt.strftime("%b %Y")

            monedas_an = sorted(df_an["moneda"].dropna().unique().tolist())

            segmentos = sorted(df_an["segmento"].dropna().unique().tolist())

            # ── Fila: Modo | Moneda ───────────────────────────────────────────
            col_modo, col_mon = st.columns([3, 1])
            with col_modo:
                modo = st.radio(
                    "Modo", ["Individual", "Comparativo"],
                    horizontal=True, key="an_modo",
                )
            with col_mon:
                if len(monedas_an) > 1:
                    moneda_an = st.radio(
                        "Moneda", monedas_an, horizontal=True, key="an_moneda",
                    )
                else:
                    moneda_an = monedas_an[0]
                    st.markdown(
                        f"<div style='font-size:12px;color:#888;padding-top:4px'>Moneda: {moneda_an}</div>",
                        unsafe_allow_html=True,
                    )

            # ── Filtro segmento ───────────────────────────────────────────────
            with st.expander("Segmento", expanded=False):
                segmentos_sel = st.multiselect(
                    "", segmentos, default=segmentos,
                    key="an_segmentos", label_visibility="collapsed",
                )

            df_an = df_an[
                (df_an["moneda"] == moneda_an) &
                (df_an["segmento"].isin(segmentos_sel) if segmentos_sel else True)
            ].copy()

            meses_an = (
                df_an.drop_duplicates("_mes")
                .sort_values("_mes")["label"]
                .tolist()
            )
            contrapartes_an = sorted(df_an["contraparte"].dropna().unique().tolist())

            if len(meses_an) < 2:
                st.info("Necesitás al menos 2 meses de datos para ver la evolución.")
            else:

                desde_an, hasta_an = st.select_slider(
                    "Período",
                    options=meses_an,
                    value=(meses_an[0], meses_an[-1]),
                    key="an_rango",
                )
                desde_mes_an = df_an.loc[df_an["label"] == desde_an, "_mes"].iloc[0]
                hasta_mes_an = df_an.loc[df_an["label"] == hasta_an, "_mes"].iloc[0]
                df_an_f = df_an[(df_an["_mes"] >= desde_mes_an) & (df_an["_mes"] <= hasta_mes_an)]

                # Rango completo de meses (para rellenar con 0 los meses sin actividad)
                full_range = pd.date_range(desde_mes_an + "-01", hasta_mes_an + "-01", freq="MS")
                df_rango = pd.DataFrame({
                    "_mes":  full_range.strftime("%Y-%m"),
                    "label": full_range.strftime("%b %Y"),
                })
                mes_order_an = df_rango["label"].tolist()

                color_an = {"ARS": "#094293", "USD": "#00cc66"}.get(moneda_an, "#094293")

                if modo == "Individual":
                    cp_ind = st.selectbox(
                        "Contraparte", [None] + contrapartes_an, index=0,
                        format_func=lambda x: "Elegí una contraparte..." if x is None else x,
                        key="an_cp_ind",
                    )
                    if cp_ind is None:
                        st.info("Seleccioná una contraparte para ver la evolución mensual.")
                    else:
                        df_agg = (
                            df_an_f[df_an_f["contraparte"] == cp_ind]
                            .groupby("_mes", as_index=False)["bruto"].sum()
                        )
                        df_plot = (
                            df_rango.merge(df_agg, on="_mes", how="left")
                            .fillna({"bruto": 0})
                        )
                        max_v = df_plot["bruto"].abs().max()
                        if max_v >= 1e9:
                            df_plot["y"] = df_plot["bruto"] / 1e9; y_ttl = f"Billones {moneda_an}"; y_f = ",.2f"
                        elif max_v >= 1e6:
                            df_plot["y"] = df_plot["bruto"] / 1e6; y_ttl = f"Millones {moneda_an}"; y_f = ",.1f"
                        else:
                            df_plot["y"] = df_plot["bruto"]; y_ttl = moneda_an; y_f = ",.0f"
                        chart = (
                            alt.Chart(df_plot)
                            .mark_line(color=color_an, strokeWidth=2,
                                       point=alt.OverlayMarkDef(size=60, color=color_an))
                            .encode(
                                x=alt.X("label:O", sort=mes_order_an, axis=alt.Axis(labelAngle=-45, title=None)),
                                y=alt.Y("y:Q", axis=alt.Axis(title=y_ttl, format=y_f, tickCount=6)),
                                tooltip=[alt.Tooltip("label:O", title="Mes"),
                                         alt.Tooltip("y:Q", format=y_f, title=y_ttl)],
                            )
                            .properties(height=400)
                        )
                        st.altair_chart(chart, use_container_width=True)

                else:  # Comparativo
                    cps_sel = st.multiselect(
                        "Contrapartes", contrapartes_an, default=[],
                        placeholder="Elegí una o más...", key="an_cp_comp",
                    )
                    if not cps_sel:
                        st.info("Seleccioná al menos una contraparte para comparar.")
                    else:
                        # Rellenar con 0 para cada contraparte en el rango completo
                        pieces = []
                        for cp in cps_sel:
                            df_agg = (
                                df_an_f[df_an_f["contraparte"] == cp]
                                .groupby("_mes", as_index=False)["bruto"].sum()
                            )
                            df_cp = df_rango.merge(df_agg, on="_mes", how="left").fillna({"bruto": 0})
                            df_cp["contraparte"] = cp
                            pieces.append(df_cp)
                        df_plot = pd.concat(pieces, ignore_index=True)

                        _step = 20_000_000_000
                        _max  = int(df_plot["bruto"].max())
                        _ticks = list(range(0, _max + _step, _step))
                        chart = (
                            alt.Chart(df_plot)
                            .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=60))
                            .encode(
                                x=alt.X("label:O", sort=mes_order_an, axis=alt.Axis(labelAngle=-45, title=None)),
                                y=alt.Y("bruto:Q",
                                        title=f"Bruto ({moneda_an})",
                                        axis=alt.Axis(format=",.0f", values=_ticks)),
                                color=alt.Color("contraparte:N",
                                                scale=alt.Scale(scheme="tableau20"),
                                                legend=alt.Legend(orient="top", labelFontSize=11)),
                                tooltip=[
                                    alt.Tooltip("label:O", title="Mes"),
                                    alt.Tooltip("contraparte:N", title="Contraparte"),
                                    alt.Tooltip("bruto:Q", format=",.0f", title=f"Bruto ({moneda_an})"),
                                ],
                            )
                            .properties(height=400)
                        )
                        st.altair_chart(chart, use_container_width=True)

    with tab_fva:
        _render_flujo_vs_aum()


# ==========================================
# Flujo vs AuM (Fondos)
# ==========================================

@st.cache_data(ttl=300, show_spinner=False)
def _cargar_fondos_flujo_aum():
    """
    Devuelve:
      - fondos: lista de emisores con segmento=Fondos
      - df_flujo: (emisor, fecha, bruto) — trades individuales ARS
      - df_aum:   (emisor, fecha_snapshot, valuacion) — AuM diario agregado por emisor
    """
    db_cf  = get_db_cashflow()
    db_val = get_db_valuaciones()

    # 1. Fondos
    fondos = list(dict.fromkeys(
        d["contraparte"] for d in db_cf["Contrapartes"].find(
            {"segmento": "Fondos"}, {"_id": 0, "contraparte": 1}
        )
    ))
    if not fondos:
        return [], pd.DataFrame(), pd.DataFrame()

    # 2. Flujo ARS — nivel trade
    flujo_docs = list(db_cf["Flujo"].find(
        {"contraparte": {"$in": fondos}, "moneda": "ARS"},
        {"_id": 0, "contraparte": 1, "concertacion": 1, "bruto": 1}
    ))
    if flujo_docs:
        df_fl = pd.DataFrame(flujo_docs)
        df_fl["fecha"]  = pd.to_datetime(df_fl["concertacion"], errors="coerce")
        df_fl["bruto"]  = pd.to_numeric(df_fl["bruto"], errors="coerce").fillna(0)
        df_fl["emisor"] = df_fl["contraparte"]
        df_flujo = df_fl[["emisor", "fecha", "bruto"]].dropna(subset=["fecha"]).sort_values("fecha")
    else:
        df_flujo = pd.DataFrame()

    # 3. AuM diario por emisor (suma valuacion de todas las unidades FCI del emisor)
    assets_docs = list(db_val["Assets"].find(
        {"EMISOR": {"$in": fondos}, "CARTERA": "CARTERA FCI"},
        {"_id": 0, "unidad": 1, "EMISOR": 1}
    ))
    if assets_docs:
        df_assets   = pd.DataFrame(assets_docs)
        unidades    = df_assets["unidad"].tolist()
        emisor_map  = df_assets.set_index("unidad")["EMISOR"].to_dict()
        aum_docs    = list(db_val["AuM"].find(
            {"unidad": {"$in": unidades}},
            {"_id": 0, "unidad": 1, "valuacion": 1, "fecha_snapshot": 1}
        ))
        if aum_docs:
            df_a = pd.DataFrame(aum_docs)
            df_a["valuacion"]      = pd.to_numeric(df_a["valuacion"], errors="coerce").fillna(0)
            df_a["fecha_snapshot"] = pd.to_datetime(df_a["fecha_snapshot"], errors="coerce")
            df_a["emisor"]         = df_a["unidad"].map(emisor_map)
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


def _render_flujo_vs_aum():
    fondos, df_flujo, df_aum = _cargar_fondos_flujo_aum()

    if not fondos:
        st.caption("Sin contrapartes con segmento=Fondos.")
        return

    col_emisor, col_desde, col_hasta = st.columns([2, 1, 1])
    with col_emisor:
        emisor = st.selectbox(
            "Emisor", sorted(fondos),
            label_visibility="collapsed",
            key="fva_emisor",
        )

    fl = df_flujo[df_flujo["emisor"] == emisor].copy() if not df_flujo.empty else pd.DataFrame()
    am = df_aum[df_aum["emisor"] == emisor].copy()    if not df_aum.empty  else pd.DataFrame()

    if am.empty:
        st.caption("Sin datos de AuM para este emisor.")
        return

    data_min = am["fecha_snapshot"].min().date()
    data_max  = pd.Timestamp.today().normalize().date()

    with col_desde:
        desde = st.date_input(
            "Desde", value=data_min,
            min_value=data_min, max_value=data_max,
            key="fva_desde",
        )
    with col_hasta:
        hasta = st.date_input(
            "Hasta", value=data_max,
            min_value=data_min, max_value=data_max,
            key="fva_hasta",
        )

    if desde > hasta:
        st.warning("'Desde' debe ser anterior a 'Hasta'.")
        return

    start_date = pd.Timestamp(desde)
    end_date   = pd.Timestamp(hasta)

    # AuM forward-fill → línea continua sin gaps
    am_ff = (
        am[["fecha_snapshot", "valuacion"]]
        .set_index("fecha_snapshot")
        .reindex(pd.date_range(start_date, end_date, freq="D"))
        .ffill()
        .reset_index()
        .rename(columns={"index": "fecha_snapshot"})
        .dropna()
    )

    # Flujo: solo días con operaciones en el rango seleccionado
    if not fl.empty:
        fl = fl[(fl["fecha"] >= start_date) & (fl["fecha"] <= end_date)].sort_values("fecha")
        fl = fl.groupby("fecha", as_index=False)["bruto"].sum()

    # Formato Y dinámico
    y_expr = (
        "abs(datum.value) >= 1e9 ? format(datum.value/1e9, ',.2f') + 'B' : "
        "abs(datum.value) >= 1e6 ? format(datum.value/1e6, ',.1f') + 'M' : "
        "format(datum.value, ',.0f')"
    )
    rango_dias = (end_date - start_date).days
    if rango_dias <= 30:
        tick_count = "day"
        x_fmt = "%d %b"
    elif rango_dias <= 120:
        tick_count = "week"
        x_fmt = "%d %b"
    else:
        tick_count = "month"
        x_fmt = "%b %Y"
    x_axis = alt.Axis(format=x_fmt, labelAngle=-45, tickCount=tick_count, title=None)

    layers = []

    # Barras flujo — verde entrada / rojo salida
    if not fl.empty:
        bars = (
            alt.Chart(fl)
            .mark_bar(opacity=0.85)
            .encode(
                x=alt.X("fecha:T", axis=x_axis),
                y=alt.Y("bruto:Q", title="Flujo ARS",
                        axis=alt.Axis(labelExpr=y_expr)),
                color=alt.condition(
                    alt.datum.bruto > 0,
                    alt.value("#00cc66"),
                    alt.value("#e05252"),
                ),
                tooltip=[
                    alt.Tooltip("fecha:T",  title="Fecha", format="%d/%m/%Y"),
                    alt.Tooltip("bruto:Q",  title="Flujo ARS", format=",.0f"),
                ],
            )
        )
        layers.append(bars)

    # Dominio AuM con padding visible (no arranca en 0)
    aum_min = am_ff["valuacion"].min()
    aum_max = am_ff["valuacion"].max()
    aum_pad = (aum_max - aum_min) * 0.15 if aum_max > aum_min else aum_max * 0.05
    aum_scale = alt.Scale(domain=[aum_min - aum_pad, aum_max + aum_pad], zero=False)

    # Línea AuM continua — naranja
    line_aum = (
        alt.Chart(am_ff)
        .mark_line(color="#f4a261", strokeWidth=2)
        .encode(
            x=alt.X("fecha_snapshot:T", axis=x_axis),
            y=alt.Y("valuacion:Q", title="AuM",
                    scale=aum_scale,
                    axis=alt.Axis(labelExpr=y_expr)),
            tooltip=[
                alt.Tooltip("fecha_snapshot:T", title="Fecha", format="%d/%m/%Y"),
                alt.Tooltip("valuacion:Q",       title="AuM",  format=",.0f"),
            ],
        )
    )
    layers.append(line_aum)

    # ── Leyenda con valores actuales ─────────────────────────────────────────
    def fmt_val(v):
        if abs(v) >= 1e9: return f"{v/1e9:,.2f}B"
        if abs(v) >= 1e6: return f"{v/1e6:,.1f}M"
        return f"{v:,.0f}"

    aum_actual   = am_ff["valuacion"].iloc[-1] if not am_ff.empty else 0
    flujo_acum   = fl["bruto"].sum()           if not fl.empty    else 0

    st.caption(f"{emisor} — FLUJO DIARIO vs AuM")
    leg1, leg2, _ = st.columns([1.2, 1.2, 4])
    with leg1:
        st.markdown(
            f"<span style='color:#f4a261;font-size:16px'>■</span> "
            f"<span style='font-size:12px;color:#aaa'>AuM actual</span><br>"
            f"<span style='font-size:13px;font-weight:600'>{fmt_val(aum_actual)}</span>",
            unsafe_allow_html=True,
        )
    with leg2:
        st.markdown(
            f"<span style='color:#00cc66;font-size:16px'>■</span> "
            f"<span style='font-size:12px;color:#aaa'>Flujo acumulado</span><br>"
            f"<span style='font-size:13px;font-weight:600'>{fmt_val(flujo_acum)}</span>",
            unsafe_allow_html=True,
        )

    chart = (
        alt.layer(*layers)
        .resolve_scale(y="independent")
        .properties(height=380)
        .interactive()
    )
    st.altair_chart(chart, use_container_width=True)


# ==========================================
# AuM
# ==========================================
@st.cache_data(ttl=300, show_spinner=False)
def _cargar_aum():
    db = get_db_valuaciones()
    docs = list(db["AuM"].find(
        {},
        {"_id": 0, "id_cuenta": 1, "cuenta": 1, "unidad": 1,
         "tipoTitulo": 1, "cantidad": 1, "precio": 1, "valuacion": 1, "fecha_snapshot": 1}
    ))
    if not docs:
        return pd.DataFrame()
    df = pd.DataFrame(docs)
    df["valuacion"] = pd.to_numeric(df["valuacion"], errors="coerce").fillna(0)
    df["cantidad"]  = pd.to_numeric(df["cantidad"],  errors="coerce").fillna(0)
    df["precio"]    = pd.to_numeric(df["precio"],    errors="coerce")
    return df


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_assets():
    """Devuelve dict {unidad: {CARTERA, EMISOR, TICKER, CLASE_ACTIVO, CALIFICACION, VENCIMIENTO}}."""
    db = get_db_valuaciones()
    docs = list(db["Assets"].find({}, {"_id": 0, "unidad": 1,
        "CARTERA": 1, "EMISOR": 1, "TICKER": 1,
        "CLASE_ACTIVO": 1, "CALIFICACION": 1, "VENCIMIENTO": 1}))
    return {d["unidad"]: d for d in docs}


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_curvas_tasa_fija():
    """Dict {ticker_corto: {fecha_vencimiento, flujo_vencimiento}} para curva=tasa_fija en Trading.Curvas."""
    docs = list(get_db()["Curvas"].find(
        {"curva": "tasa_fija"},
        {"_id": 0, "ticker_corto": 1, "fecha_vencimiento": 1, "flujo_vencimiento": 1},
    ))
    return {d["ticker_corto"]: d for d in docs}


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_curvas_cer():
    """Dict {ticker_corto: {fecha_vencimiento}} para curva=cer en Trading.Curvas."""
    docs = list(get_db()["Curvas"].find(
        {"curva": "cer"},
        {"_id": 0, "ticker_corto": 1, "fecha_vencimiento": 1},
    ))
    return {d["ticker_corto"]: d for d in docs}


def _render_snapshot_fci(df_fci, key_prefix):
    """Tabla + torta de valuacion FCI por EMISOR para un df ya filtrado por fecha."""
    if df_fci.empty:
        st.info("Sin posiciones FCI para esta fecha.")
        return

    resumen = (
        df_fci.groupby("EMISOR", as_index=False)["valuacion"]
        .sum()
        .sort_values("valuacion", ascending=False)
        .reset_index(drop=True)
    )
    total = resumen["valuacion"].sum()
    resumen["% del Total"] = (resumen["valuacion"] / total * 100).round(2)
    resumen["Valuación"]   = resumen["valuacion"].apply(lambda v: f"{v:,.0f}")
    resumen["% del Total"] = resumen["% del Total"].apply(lambda v: f"{v:.2f}%")

    col_tabla, col_pie = st.columns([2, 1])
    with col_tabla:
        st.markdown(
            f"<div style='font-size:13px;color:#888;margin-bottom:4px'>Total FCI</div>"
            f"<div style='font-size:26px;font-weight:700;color:#094293'>${total:,.0f}</div>",
            unsafe_allow_html=True,
        )
        st.dataframe(
            resumen[["EMISOR", "Valuación", "% del Total"]],
            hide_index=True, use_container_width=True,
            height=df_height(len(resumen)),
        )
    with col_pie:
        df_pie = resumen[resumen["valuacion"] > 0][["EMISOR", "valuacion"]].copy()
        df_pie["pct"]       = (df_pie["valuacion"] / total * 100).round(1)
        df_pie["pct_label"] = df_pie["pct"].apply(lambda x: f"{x:.1f}%")
        base = alt.Chart(df_pie).encode(
            theta=alt.Theta("valuacion:Q", stack=True),
            color=alt.Color("EMISOR:N", scale=alt.Scale(scheme="tableau20"),
                            legend=alt.Legend(orient="bottom", columns=2, labelFontSize=10)),
        )
        arc  = base.mark_arc(innerRadius=45, outerRadius=100).encode(
            tooltip=[alt.Tooltip("EMISOR:N", title="Emisor"),
                     alt.Tooltip("valuacion:Q", title="Valuación", format=",.0f"),
                     alt.Tooltip("pct:Q", title="%", format=".1f")]
        )
        text = base.mark_text(radius=75, size=11, color="white").encode(
            text=alt.Text("pct_label:N"),
        )
        st.altair_chart((arc + text).properties(height=380, padding={"top": 10}),
                        use_container_width=True)


def _render_barras_rango_fci(df_fci_all, color_field, key_prefix):
    """Barras apiladas por fecha con rango slider. color_field: 'EMISOR' o None (total)."""
    fechas = sorted(df_fci_all["fecha_snapshot"].unique())
    if len(fechas) < 2:
        st.info("Necesitás al menos 2 fechas de datos.")
        return

    fecha_desde, fecha_hasta = st.select_slider(
        "Período",
        options=fechas,
        value=(fechas[0], fechas[-1]),
        key=f"{key_prefix}_rango",
    )
    df_r = df_fci_all[
        (df_fci_all["fecha_snapshot"] >= fecha_desde) &
        (df_fci_all["fecha_snapshot"] <= fecha_hasta)
    ].copy()

    fechas_rango = sorted(df_r["fecha_snapshot"].unique())

    if color_field:
        df_plot = df_r.groupby(["fecha_snapshot", color_field], as_index=False)["valuacion"].sum()
        chart = (
            alt.Chart(df_plot)
            .mark_bar()
            .encode(
                x=alt.X("fecha_snapshot:O", title="Fecha", sort=fechas_rango,
                         axis=alt.Axis(labelAngle=-45)),
                y=alt.Y("valuacion:Q", title="Valuación (ARS)", stack=True,
                         axis=alt.Axis(format=",.0f")),
                color=alt.Color(f"{color_field}:N", scale=alt.Scale(scheme="tableau20"),
                                legend=alt.Legend(orient="top", columns=4, labelFontSize=10)),
                tooltip=[alt.Tooltip("fecha_snapshot:O", title="Fecha"),
                         alt.Tooltip(f"{color_field}:N", title=color_field),
                         alt.Tooltip("valuacion:Q", format=",.0f", title="Valuación")],
            )
            .properties(height=420)
        )
    else:
        df_plot = df_r.groupby("fecha_snapshot", as_index=False)["valuacion"].sum()
        chart = (
            alt.Chart(df_plot)
            .mark_bar(color="#094293")
            .encode(
                x=alt.X("fecha_snapshot:O", title="Fecha", sort=fechas_rango,
                         axis=alt.Axis(labelAngle=-45)),
                y=alt.Y("valuacion:Q", title="Valuación total FCI (ARS)",
                         axis=alt.Axis(format=",.0f")),
                tooltip=[alt.Tooltip("fecha_snapshot:O", title="Fecha"),
                         alt.Tooltip("valuacion:Q", format=",.0f", title="Valuación")],
            )
            .properties(height=420)
        )

    st.altair_chart(chart, use_container_width=True)


def vista_aum():
    st.markdown("## ACAQuant | AuM")

    df = _cargar_aum()
    if df.empty:
        st.warning("Sin datos. Ejecutá `main_aum.py` para cargar las posiciones.")
        return

    assets = _cargar_assets()
    df["CARTERA"] = df["unidad"].map(lambda u: assets.get(u, {}).get("CARTERA", ""))
    df["EMISOR"]  = df["unidad"].map(lambda u: assets.get(u, {}).get("EMISOR",  ""))

    df_fci_all = df[df["CARTERA"] == "CARTERA FCI"].copy()

    tab_fci, tab_stock_soc, tab_tasa_fija, tab_cer, tab_rv = st.tabs(["FCI", "Análisis SG", "Tasa Fija", "CER", "Renta Variable"])

    # ── Tab 1: FCI (snapshot + stock lado a lado) ─────────────────────────────
    with tab_fci:
        snapshots = sorted(df_fci_all["fecha_snapshot"].dropna().unique())
        fechas_all = sorted(df_fci_all["fecha_snapshot"].dropna().unique())

        if not snapshots:
            st.info("Sin datos FCI.")
        else:
            # ── Gráfico a ancho completo arriba ───────────────────────────────
            if len(fechas_all) >= 2:
                fecha_desde, fecha_hasta = st.select_slider(
                    "Período",
                    options=fechas_all,
                    value=(fechas_all[0], fechas_all[-1]),
                    key="aum_fci_rango",
                )
                df_r = df_fci_all[
                    (df_fci_all["fecha_snapshot"] >= fecha_desde) &
                    (df_fci_all["fecha_snapshot"] <= fecha_hasta)
                ]
                df_plot = df_r.groupby("fecha_snapshot", as_index=False)["valuacion"].sum()
                fechas_rango = sorted(df_plot["fecha_snapshot"].unique())
                chart = (
                    alt.Chart(df_plot)
                    .mark_line(color="#094293", strokeWidth=2,
                               point=alt.OverlayMarkDef(size=60, color="#094293"))
                    .encode(
                        x=alt.X("fecha_snapshot:O", title="Fecha", sort=fechas_rango,
                                axis=alt.Axis(labelAngle=-45)),
                        y=alt.Y("valuacion:Q", title="Valuación total FCI",
                                scale=alt.Scale(zero=False),
                                axis=alt.Axis(format=",.0f")),
                        tooltip=[alt.Tooltip("fecha_snapshot:O", title="Fecha"),
                                 alt.Tooltip("valuacion:Q", format=",.0f", title="Valuación")],
                    )
                    .properties(height=400)
                )
                st.altair_chart(chart, use_container_width=True)

            st.divider()

            # ── Dos columnas al mismo nivel ───────────────────────────────────
            fecha_sel = st.select_slider("Fecha snapshot", options=snapshots,
                                         value=snapshots[-1], key="aum_snap_fecha")
            df_fci_dia = df_fci_all[df_fci_all["fecha_snapshot"] == fecha_sel]

            resumen = (
                df_fci_dia.groupby("EMISOR", as_index=False)["valuacion"]
                .sum()
                .sort_values("valuacion", ascending=False)
                .reset_index(drop=True)
            )
            total = resumen["valuacion"].sum()
            resumen["Val."] = resumen["valuacion"].apply(lambda v: f"{v:,.0f}")
            resumen["%"]    = (resumen["valuacion"] / total * 100).apply(lambda v: f"{v:.1f}%")

            h_emisor = 38 + 35 * len(resumen)

            col_izq, col_der = st.columns(2)

            with col_izq:
                st.markdown(
                    f"<div style='font-size:12px;color:#888'>Total FCI</div>"
                    f"<div style='font-size:22px;font-weight:700;color:#094293'>${total:,.0f}</div>",
                    unsafe_allow_html=True,
                )
                ev_fci = st.dataframe(
                    resumen[["EMISOR", "Val.", "%"]],
                    hide_index=True, use_container_width=True,
                    height=h_emisor,
                    on_select="rerun",
                    selection_mode="single-row",
                    key="aum_fci_tabla",
                )

            with col_der:
                sel_rows = ev_fci.selection.rows if ev_fci.selection.rows else []
                if not sel_rows:
                    with st.container(height=h_emisor, border=False):
                        st.markdown(
                            "<div style='font-size:13px;color:#888;padding:8px'>"
                            "Seleccioná un emisor para ver los fondos.</div>",
                            unsafe_allow_html=True,
                        )
                else:
                    emisor_det = resumen.iloc[sel_rows[0]]["EMISOR"]
                    df_det_src = df_fci_dia[df_fci_dia["EMISOR"] == emisor_det].copy()
                    df_det_src["TICKER"] = df_det_src["unidad"].map(
                        lambda u: assets.get(u, {}).get("TICKER", u)
                    )
                    df_det = (
                        df_det_src.groupby("TICKER", as_index=False)["valuacion"]
                        .sum()
                        .sort_values("valuacion", ascending=False)
                        .reset_index(drop=True)
                    )
                    with st.container(height=h_emisor, border=False):
                        st.markdown(
                            f"<div style='font-size:11px;color:#888;padding:2px 4px 6px'>{emisor_det}</div>",
                            unsafe_allow_html=True,
                        )
                        for _, row in df_det.iterrows():
                            ticker_nom = row["TICKER"]
                            val_total  = row["valuacion"]
                            with st.expander(f"{ticker_nom}   —   ${val_total:,.0f}"):
                                df_cuentas = (
                                    df_det_src[df_det_src["TICKER"] == ticker_nom]
                                    [["cuenta", "valuacion"]]
                                    .groupby("cuenta", as_index=False)["valuacion"].sum()
                                    .sort_values("valuacion", ascending=False)
                                    .reset_index(drop=True)
                                )
                                for _, cr in df_cuentas.iterrows():
                                    st.markdown(
                                        f"<div style='display:flex;justify-content:space-between;"
                                        f"padding:2px 4px;font-size:13px'>"
                                        f"<span>{cr['cuenta']}</span>"
                                        f"<span style='font-weight:600'>${cr['valuacion']:,.0f}</span>"
                                        f"</div>",
                                        unsafe_allow_html=True,
                                    )

    # ── Tab 2: Stock Soc. Gerente ─────────────────────────────────────────────
    with tab_stock_soc:
        snapshots  = sorted(df_fci_all["fecha_snapshot"].dropna().unique())
        emisores   = sorted(df_fci_all["EMISOR"].dropna().unique())
        fechas_all = snapshots

        if not snapshots:
            st.info("Sin datos FCI.")
        elif len(fechas_all) < 2:
            st.info("Necesitás al menos 2 fechas de datos.")
        else:
            modo = st.radio(
                "Modo", ["Individual", "Comparativo (base 100)"],
                horizontal=True, key="aum_soc_modo",
            )

            fecha_desde, fecha_hasta = st.select_slider(
                "Período",
                options=fechas_all,
                value=(fechas_all[0], fechas_all[-1]),
                key="aum_soc_rango",
            )

            if modo == "Individual":
                emisor_sel = st.selectbox(
                    "Soc. Gerente", [None] + emisores, index=0,
                    format_func=lambda x: "Elegí una Soc. Gerente..." if x is None else x,
                    key="aum_soc_emisores",
                )
                if emisor_sel is None:
                    st.info("Seleccioná una Soc. Gerente para ver la evolución.")
                else:
                    df_r = df_fci_all[
                        (df_fci_all["fecha_snapshot"] >= fecha_desde) &
                        (df_fci_all["fecha_snapshot"] <= fecha_hasta) &
                        (df_fci_all["EMISOR"] == emisor_sel)
                    ]
                    df_plot = df_r.groupby("fecha_snapshot", as_index=False)["valuacion"].sum()
                    f_rango = sorted(df_plot["fecha_snapshot"].unique())
                    chart = (
                        alt.Chart(df_plot)
                        .mark_line(color="#094293", strokeWidth=2,
                                   point=alt.OverlayMarkDef(size=60, color="#094293"))
                        .encode(
                            x=alt.X("fecha_snapshot:O", title="Fecha", sort=f_rango,
                                    axis=alt.Axis(labelAngle=-45)),
                            y=alt.Y("valuacion:Q", title="Valuación (ARS)",
                                    scale=alt.Scale(zero=False),
                                    axis=alt.Axis(format=",.0f")),
                            tooltip=[alt.Tooltip("fecha_snapshot:O", title="Fecha"),
                                     alt.Tooltip("valuacion:Q", format=",.0f", title="Valuación")],
                        )
                        .properties(height=420)
                    )
                    st.altair_chart(chart, use_container_width=True)

            else:  # Comparativo base 100
                emisores_sel = st.multiselect(
                    "Soc. Gerente", emisores, default=[],
                    placeholder="Elegí una o más...", key="aum_soc_emisores_comp",
                )
                if not emisores_sel:
                    st.info("Seleccioná al menos una Soc. Gerente para comparar.")
                else:
                    df_r = df_fci_all[
                        (df_fci_all["fecha_snapshot"] >= fecha_desde) &
                        (df_fci_all["fecha_snapshot"] <= fecha_hasta) &
                        (df_fci_all["EMISOR"].isin(emisores_sel))
                    ]
                    df_plot = df_r.groupby(["fecha_snapshot", "EMISOR"], as_index=False)["valuacion"].sum()
                    # Normalizar a base 100 desde la primera fecha del rango para cada emisor
                    bases = (
                        df_plot.sort_values("fecha_snapshot")
                        .groupby("EMISOR")["valuacion"]
                        .first()
                        .rename("base")
                    )
                    df_plot = df_plot.join(bases, on="EMISOR")
                    df_plot["base100"] = df_plot["valuacion"] / df_plot["base"] * 100
                    f_rango = sorted(df_plot["fecha_snapshot"].unique())
                    chart = (
                        alt.Chart(df_plot)
                        .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=60))
                        .encode(
                            x=alt.X("fecha_snapshot:O", title="Fecha", sort=f_rango,
                                    axis=alt.Axis(labelAngle=-45)),
                            y=alt.Y("base100:Q", title="Índice (base 100)",
                                    scale=alt.Scale(zero=False),
                                    axis=alt.Axis(format=".1f")),
                            color=alt.Color("EMISOR:N", scale=alt.Scale(scheme="tableau20"),
                                            legend=alt.Legend(orient="top", labelFontSize=11)),
                            tooltip=[
                                alt.Tooltip("fecha_snapshot:O", title="Fecha"),
                                alt.Tooltip("EMISOR:N", title="Emisor"),
                                alt.Tooltip("base100:Q", format=".2f", title="Base 100"),
                                alt.Tooltip("valuacion:Q", format=",.0f", title="Valuación (ARS)"),
                            ],
                        )
                        .properties(height=420)
                    )
                    st.altair_chart(chart, use_container_width=True)

    # ── Tab 3: Tasa Fija ──────────────────────────────────────────────────────
    with tab_tasa_fija:
        curvas_map = _cargar_curvas_tasa_fija()
        if not curvas_map:
            st.info("Sin instrumentos de tasa_fija en Trading.Curvas.")
        else:
            tasa_fija_set = set(curvas_map.keys())
            unidades_tf = {u for u, a in assets.items() if a.get("TICKER") in tasa_fija_set}
            df_tf_all = df[df["unidad"].isin(unidades_tf)].copy()

            if df_tf_all.empty:
                st.info("Sin posiciones de Tasa Fija en AuM.")
            else:
                df_tf_all["ticker_corto"] = df_tf_all["unidad"].map(
                    lambda u: assets.get(u, {}).get("TICKER", "")
                )
                df_tf_all["fecha_venc"] = df_tf_all["ticker_corto"].map(
                    lambda t: (curvas_map.get(t, {}).get("fecha_vencimiento") or "")[:10]
                )
                df_tf_all["flujo_venc"] = df_tf_all["ticker_corto"].map(
                    lambda t: float(curvas_map.get(t, {}).get("flujo_vencimiento") or 0)
                )
                df_tf_all["pago_final"] = df_tf_all["cantidad"] * df_tf_all["flujo_venc"] / 100

                # siempre el snapshot más reciente
                fecha_sel_tf = df_tf_all["fecha_snapshot"].dropna().max()
                df_tf = df_tf_all[df_tf_all["fecha_snapshot"] == fecha_sel_tf].copy()

                # tabla consolidada por ticker
                tbl = (
                    df_tf.groupby(["ticker_corto", "fecha_venc"], as_index=False)
                    .agg(valuacion=("valuacion", "sum"),
                         cantidad=("cantidad", "sum"),
                         pago_final=("pago_final", "sum"))
                    .sort_values("fecha_venc")
                    .reset_index(drop=True)
                )

                # chart data — solo cobros al vencimiento
                chart_rows = []
                for _, row in tbl.iterrows():
                    if row["pago_final"] > 0 and row["fecha_venc"]:
                        chart_rows.append({"fecha": row["fecha_venc"], "monto": row["pago_final"],
                                           "ticker": row["ticker_corto"]})

                total_val_tf = tbl["valuacion"].sum()
                total_cobro  = tbl["pago_final"].sum()
                col_metrics_tf, col_toggle_tf = st.columns([5, 1])
                with col_metrics_tf:
                    st.markdown(
                        f"<div style='display:flex;gap:40px;margin-bottom:8px'>"
                        f"<div><span style='font-size:11px;color:#888'>Valuación actual</span><br>"
                        f"<span style='font-size:17px;font-weight:600'>${total_val_tf:,.0f}</span></div>"
                        f"<div><span style='font-size:11px;color:#888'>Cobro proyectado</span><br>"
                        f"<span style='font-size:17px;font-weight:600'>${total_cobro:,.0f}</span></div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                with col_toggle_tf:
                    st.markdown(
                        "<div style='display:flex;justify-content:flex-end;padding-top:14px'>",
                        unsafe_allow_html=True,
                    )
                    ver_vn_tf = st.toggle("Valor Nominal", key="tf_toggle_vn")
                    st.markdown("</div>", unsafe_allow_html=True)

                col_src_tf = "cantidad" if ver_vn_tf else "valuacion"
                col_lbl_tf = "VN" if ver_vn_tf else "Valuación"
                fmt_tf = (lambda v: f"{v:,.2f}") if ver_vn_tf else (lambda v: f"${v:,.0f}")

                h_tbl = 38 + 35 * len(tbl)

                # ── fila 1: tabla tickers | tabla cuentas (mismo tamaño) ──
                col_tbl, col_det = st.columns([2, 3])

                with col_tbl:
                    tbl_display = tbl[["ticker_corto", "fecha_venc", col_src_tf]].copy()
                    tbl_display[col_src_tf] = tbl_display[col_src_tf].apply(fmt_tf)
                    tbl_display.rename(columns={
                        "ticker_corto": "Ticker",
                        "fecha_venc":   "Vencimiento",
                        col_src_tf:     col_lbl_tf,
                    }, inplace=True)
                    ev_tf = st.dataframe(
                        tbl_display, hide_index=True, use_container_width=True,
                        height=h_tbl, on_select="rerun", selection_mode="single-row",
                        key="tf_tabla",
                        column_config={
                            "Ticker":      st.column_config.TextColumn(width="small"),
                            "Vencimiento": st.column_config.TextColumn(width="small"),
                            col_lbl_tf:    st.column_config.TextColumn(width="small"),
                        },
                    )

                with col_det:
                    sel_tf = ev_tf.selection.rows if ev_tf.selection.rows else []
                    if sel_tf:
                        ticker_det = tbl.iloc[sel_tf[0]]["ticker_corto"]
                        df_det_src = df_tf[df_tf["ticker_corto"] == ticker_det]
                        label_tf = ticker_det
                    else:
                        df_det_src = df_tf
                        label_tf = "Todas las posiciones"
                    df_det = (
                        df_det_src
                        .groupby("cuenta", as_index=False)
                        .agg(valuacion=("valuacion", "sum"), cantidad=("cantidad", "sum"))
                        .sort_values(col_src_tf, ascending=False)
                        .reset_index(drop=True)
                    )
                    df_det[col_lbl_tf] = df_det[col_src_tf].apply(fmt_tf)
                    st.dataframe(
                        df_det[["cuenta", col_lbl_tf]].rename(columns={"cuenta": "Cuenta"}),
                        hide_index=True, use_container_width=True,
                        height=h_tbl,
                    )

                # ── fila 2: gráfico a ancho completo ─────────────────────
                if chart_rows:
                    df_chart = pd.DataFrame(chart_rows)
                    fechas_ord = sorted(df_chart["fecha"].unique())
                    bars = (
                        alt.Chart(df_chart)
                        .mark_bar(color="#094293")
                        .encode(
                            x=alt.X("fecha:O", title=None, sort=fechas_ord,
                                    axis=alt.Axis(labelAngle=-45)),
                            y=alt.Y("monto:Q", title="ARS", stack=True,
                                    axis=alt.Axis(format=",.0f")),
                            tooltip=[
                                alt.Tooltip("fecha:O", title="Vencimiento"),
                                alt.Tooltip("ticker:N", title="Ticker"),
                                alt.Tooltip("monto:Q", format=",.0f", title="Cobro (ARS)"),
                            ],
                        )
                        .properties(
                            height=320,
                            title=alt.TitleParams("Amortizaciones — Tasa Fija",
                                                  anchor="start", fontSize=13, fontWeight=600),
                        )
                    )
                    st.altair_chart(bars, use_container_width=True)


    # ── Tab 4: CER ────────────────────────────────────────────────────────────
    with tab_cer:
        curvas_cer = _cargar_curvas_cer()
        if not curvas_cer:
            st.info("Sin instrumentos CER en Trading.Curvas.")
        else:
            cer_set = set(curvas_cer.keys())
            unidades_cer = {u for u, a in assets.items() if a.get("TICKER") in cer_set}
            df_cer_all = df[df["unidad"].isin(unidades_cer)].copy()

            if df_cer_all.empty:
                st.info("Sin posiciones CER en AuM.")
            else:
                df_cer_all["ticker_corto"] = df_cer_all["unidad"].map(
                    lambda u: assets.get(u, {}).get("TICKER", "")
                )
                df_cer_all["fecha_venc"] = df_cer_all["ticker_corto"].map(
                    lambda t: (curvas_cer.get(t, {}).get("fecha_vencimiento") or "")[:10]
                )

                fecha_sel_cer = df_cer_all["fecha_snapshot"].dropna().max()
                df_cer = df_cer_all[df_cer_all["fecha_snapshot"] == fecha_sel_cer].copy()

                tbl_cer = (
                    df_cer.groupby(["ticker_corto", "fecha_venc"], as_index=False)
                    .agg(valuacion=("valuacion", "sum"),
                         cantidad=("cantidad", "sum"))
                    .sort_values("fecha_venc")
                    .reset_index(drop=True)
                )

                total_val_cer = tbl_cer["valuacion"].sum()
                col_metrics_cer, col_toggle_cer = st.columns([5, 1])
                with col_metrics_cer:
                    st.markdown(
                        f"<div style='margin-bottom:8px'>"
                        f"<span style='font-size:11px;color:#888'>Valuación actual</span><br>"
                        f"<span style='font-size:17px;font-weight:600'>${total_val_cer:,.0f}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                with col_toggle_cer:
                    st.markdown(
                        "<div style='display:flex;justify-content:flex-end;padding-top:14px'>",
                        unsafe_allow_html=True,
                    )
                    ver_vn_cer = st.toggle("Valor Nominal", key="cer_toggle_vn")
                    st.markdown("</div>", unsafe_allow_html=True)

                col_src_cer = "cantidad" if ver_vn_cer else "valuacion"
                col_lbl_cer = "VN" if ver_vn_cer else "Valuación"
                fmt_cer = (lambda v: f"{v:,.2f}") if ver_vn_cer else (lambda v: f"${v:,.0f}")

                h_cer = 38 + 35 * len(tbl_cer)
                col_tbl_c, col_det_c = st.columns([2, 3])

                with col_tbl_c:
                    tbl_cer_disp = tbl_cer[["ticker_corto", "fecha_venc", col_src_cer]].copy()
                    tbl_cer_disp[col_src_cer] = tbl_cer_disp[col_src_cer].apply(fmt_cer)
                    tbl_cer_disp.rename(columns={
                        "ticker_corto": "Ticker",
                        "fecha_venc":   "Vencimiento",
                        col_src_cer:    col_lbl_cer,
                    }, inplace=True)
                    ev_cer = st.dataframe(
                        tbl_cer_disp, hide_index=True, use_container_width=True,
                        height=h_cer, on_select="rerun", selection_mode="single-row",
                        key="cer_tabla",
                        column_config={
                            "Ticker":      st.column_config.TextColumn(width="small"),
                            "Vencimiento": st.column_config.TextColumn(width="small"),
                            col_lbl_cer:   st.column_config.TextColumn(width="small"),
                        },
                    )

                with col_det_c:
                    sel_cer = ev_cer.selection.rows if ev_cer.selection.rows else []
                    if sel_cer:
                        ticker_det_c = tbl_cer.iloc[sel_cer[0]]["ticker_corto"]
                        df_det_c_src = df_cer[df_cer["ticker_corto"] == ticker_det_c]
                        label_cer = ticker_det_c
                    else:
                        df_det_c_src = df_cer
                        label_cer = "Todas las posiciones"
                    df_det_c = (
                        df_det_c_src
                        .groupby("cuenta", as_index=False)
                        .agg(valuacion=("valuacion", "sum"), cantidad=("cantidad", "sum"))
                        .sort_values(col_src_cer, ascending=False)
                        .reset_index(drop=True)
                    )
                    df_det_c[col_lbl_cer] = df_det_c[col_src_cer].apply(fmt_cer)
                    st.dataframe(
                        df_det_c[["cuenta", col_lbl_cer]].rename(columns={"cuenta": "Cuenta"}),
                        hide_index=True, use_container_width=True,
                        height=h_cer,
                    )


    # ── Tab 5: Renta Variable ─────────────────────────────────────────────────
    with tab_rv:
        unidades_rv = {u for u, a in assets.items() if a.get("CLASE_ACTIVO") == "RENTA VARIABLE"}
        df_rv_all = df[df["unidad"].isin(unidades_rv)].copy()

        if df_rv_all.empty:
            st.info("Sin posiciones de Renta Variable en AuM.")
        else:
            fecha_sel_rv = df_rv_all["fecha_snapshot"].dropna().max()
            df_rv = df_rv_all[df_rv_all["fecha_snapshot"] == fecha_sel_rv].copy()

            tbl_rv = (
                df_rv.groupby("unidad", as_index=False)["valuacion"]
                .sum()
                .sort_values("valuacion", ascending=False)
                .reset_index(drop=True)
            )

            total_rv = tbl_rv["valuacion"].sum()
            st.markdown(
                f"<div style='margin-bottom:8px'>"
                f"<span style='font-size:11px;color:#888'>Valuación actual</span><br>"
                f"<span style='font-size:17px;font-weight:600'>${total_rv:,.0f}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

            h_rv = 38 + 35 * min(len(tbl_rv), 20)
            col_tbl_rv, col_det_rv = st.columns([2, 3])

            with col_tbl_rv:
                tbl_rv_disp = tbl_rv.copy()
                tbl_rv_disp["valuacion"] = tbl_rv_disp["valuacion"].apply(lambda v: f"${v:,.0f}")
                tbl_rv_disp.rename(columns={"unidad": "Unidad", "valuacion": "Valuación"}, inplace=True)
                ev_rv = st.dataframe(
                    tbl_rv_disp, hide_index=True, use_container_width=True,
                    height=h_rv, on_select="rerun", selection_mode="single-row",
                    key="rv_tabla",
                    column_config={
                        "Unidad":    st.column_config.TextColumn(width="medium"),
                        "Valuación": st.column_config.TextColumn(width="small"),
                    },
                )

            with col_det_rv:
                sel_rv = ev_rv.selection.rows if ev_rv.selection.rows else []
                if sel_rv:
                    unidad_det = tbl_rv.iloc[sel_rv[0]]["unidad"]
                    df_det_rv_src = df_rv[df_rv["unidad"] == unidad_det]
                    label_rv = unidad_det
                else:
                    df_det_rv_src = df_rv
                    label_rv = "Todas las posiciones"
                df_det_rv = (
                    df_det_rv_src
                    .groupby("cuenta", as_index=False)["valuacion"]
                    .sum()
                    .sort_values("valuacion", ascending=False)
                    .reset_index(drop=True)
                )
                df_det_rv["Valuación"] = df_det_rv["valuacion"].apply(lambda v: f"${v:,.0f}")
                st.dataframe(
                    df_det_rv[["cuenta", "Valuación"]].rename(columns={"cuenta": "Cuenta"}),
                    hide_index=True, use_container_width=True,
                    height=h_rv,
                )
def render_forward_matrix(doc):
    tickers  = doc.get("tickers", [])   # ordenados por maturity ascendente
    tasas    = doc.get("tasas", {})
    matrix   = doc.get("matrix", {})
    ts       = doc.get("updated_at")

    if len(tickers) < 2:
        st.info("Menos de 2 instrumentos con TEA disponible.")
        return

    if ts:
        st.caption(f"Última actualización: {ts.strftime('%d/%m/%Y %H:%M:%S')}")

    st.caption("MATRIZ DE TASAS FORWARD (TEA)")

    # Construir DataFrame NxN
    # Filas = instrumento largo, Columnas = instrumento corto
    data = {}
    for t_largo in tickers:
        row = {}
        for t_corto in tickers:
            val = matrix.get(t_largo, {}).get(t_corto)
            row[t_corto] = val
        data[t_largo] = row

    import numpy as np
    df = pd.DataFrame(data, index=tickers).T.astype(float)

    def fmt_cell(v):
        if pd.isna(v):
            return ""
        return f"{v:.2%}"

    # Escala rojo → amarillo → verde centrada en la mediana
    todos_vals = [v for row in data.values() for v in row.values() if v is not None and not pd.isna(v)]

    def bg_cell(v):
        if pd.isna(v) or not todos_vals:
            return ""
        vmin = min(todos_vals)
        vmax = max(todos_vals)
        p50  = sorted(todos_vals)[len(todos_vals) // 2]
        if vmax == vmin:
            return "background-color: #ffdd00; color: #000"
        # Normalizar: por debajo de p50 → [0, 0.5], por encima → [0.5, 1]
        if v <= p50:
            t = (v - vmin) / (p50 - vmin) * 0.5 if p50 > vmin else 0.5
        else:
            t = 0.5 + (v - p50) / (vmax - p50) * 0.5 if vmax > p50 else 0.5
        t = max(0.0, min(1.0, t))
        # Interpolar rojo(0) → amarillo(0.5) → verde(1)
        if t <= 0.5:
            r = 255
            g = int(t / 0.5 * 221)   # 0 → 221
            b = 0
        else:
            r = int((1 - (t - 0.5) / 0.5) * 255)
            g = int(221 + (t - 0.5) / 0.5 * (204 - 221))
            b = 0
        luminancia = 0.299 * r + 0.587 * g + 0.114 * b
        txt = "#000" if luminancia > 140 else "#fff"
        return f"background-color: rgb({r},{g},{b}); color: {txt}; font-weight: bold"

    styler = (
        df.style
        .format(fmt_cell)
        .map(bg_cell)
    )
    st.dataframe(styler, use_container_width=True, height=df_height(len(tickers) + 1))


@st.cache_data(ttl=60, show_spinner=False)
def _cargar_forwards_historico(curva):
    db = get_db()
    return list(db["ForwardsHistorico"].find(
        {"curva": curva},
        {"fecha": 1, "matrix": 1, "_id": 0}
    ))


def _render_forwards(db, key_prefix="fwd"):
    curvas_live = set(db["ForwardsLive"].distinct("curva"))
    curvas_hist = set(db["ForwardsHistorico"].distinct("curva"))
    curvas_todas = sorted(curvas_live | curvas_hist)

    if not curvas_todas:
        st.info("Sin datos. ¿El motor de forwards está corriendo?")
        return

    curva_sel = st.selectbox("Curva", curvas_todas, key=f"{key_prefix}_curva")

    tab_live, tab_hist, tab_grafico = st.tabs(["Tiempo Real", "Histórico", "Gráfico"])

    with tab_live:
        doc = db["ForwardsLive"].find_one({"curva": curva_sel})
        if doc:
            render_forward_matrix(doc)
        else:
            st.info("Sin datos en tiempo real para esta curva.")

    with tab_hist:
        fechas = sorted([
            d["fecha"] for d in db["ForwardsHistorico"].find(
                {"curva": curva_sel}, {"fecha": 1, "_id": 0}
            )
        ], reverse=True)

        if not fechas:
            st.info("Sin historial disponible aún.")
        else:
            fecha_sel = st.select_slider("Fecha", options=fechas, key=f"{key_prefix}_fecha")
            doc_hist = db["ForwardsHistorico"].find_one(
                {"curva": curva_sel, "fecha": fecha_sel}
            )
            if doc_hist:
                render_forward_matrix(doc_hist)

    with tab_grafico:
        # Cargar todos los docs históricos de esta curva (cacheado 60s)
        docs_hist = _cargar_forwards_historico(curva_sel)
        if not docs_hist:
            st.info("Sin historial disponible aún.")
        else:
            # Construir lista de pares disponibles desde el doc más reciente
            doc_ref = max(docs_hist, key=lambda d: d["fecha"])
            matrix_ref = doc_ref.get("matrix", {})
            pares = []
            for t_largo, inner in matrix_ref.items():
                for t_corto, val in inner.items():
                    if val is not None:
                        pares.append(f"{t_largo} → {t_corto}")
            pares = sorted(pares)

            if not pares:
                st.info("Sin pares disponibles en la matriz.")
            else:
                pares_sel = st.multiselect(
                    "Pares", pares, default=pares[:2] if len(pares) >= 2 else pares,
                    key=f"{key_prefix}_pares"
                )
                if not pares_sel:
                    st.info("Seleccioná al menos un par.")
                else:
                    # Armar DataFrame fecha × par → forward rate
                    rows = []
                    for doc in docs_hist:
                        fecha = doc["fecha"]
                        matrix = doc.get("matrix", {})
                        for par in pares_sel:
                            t_largo, t_corto = par.split(" → ")
                            val = matrix.get(t_largo, {}).get(t_corto)
                            if val is not None:
                                rows.append({"fecha": fecha, "par": par, "forward": val * 100})
                    if not rows:
                        st.info("Sin datos para los pares seleccionados.")
                    else:
                        df_fwd = pd.DataFrame(rows).sort_values("fecha")
                        fechas_ord = sorted(df_fwd["fecha"].unique())
                        chart = (
                            alt.Chart(df_fwd)
                            .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=40))
                            .encode(
                                x=alt.X("fecha:O", title="Fecha", sort=fechas_ord,
                                        axis=alt.Axis(labelAngle=-45)),
                                y=alt.Y("forward:Q", title="Tasa Forward (%)",
                                        axis=alt.Axis(format=".2f")),
                                color=alt.Color("par:N",
                                                scale=alt.Scale(scheme="tableau10"),
                                                legend=alt.Legend(orient="top")),
                                tooltip=[
                                    alt.Tooltip("fecha:O", title="Fecha"),
                                    alt.Tooltip("par:N", title="Par"),
                                    alt.Tooltip("forward:Q", title="Forward (%)", format=".3f"),
                                ],
                            )
                            .properties(height=420)
                        )
                        st.altair_chart(chart, use_container_width=True)


@st.fragment(run_every=30)
def vista_forwards():
    db = get_db()
    st.markdown("## ACAQuant | Forwards")
    _render_forwards(db, key_prefix="fwd_page")


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_volumenes_diarios():
    """Suma de money por fecha y curva desde TimeSales, solo tickers en Trading.Curvas."""
    db = get_db()
    ticker_curva = {
        d["ticker"]: d["curva"]
        for d in db["Curvas"].find({}, {"ticker": 1, "curva": 1})
    }
    if not ticker_curva:
        return pd.DataFrame()

    pipeline = [
        {"$match": {"ticker": {"$in": list(ticker_curva.keys())}, "money": {"$gt": 0}}},
        {"$group": {
            "_id": {
                "fecha": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
                "ticker": "$ticker",
            },
            "money": {"$sum": "$money"},
        }},
    ]
    rows = []
    for r in db["TimeSales"].aggregate(pipeline):
        ticker = r["_id"]["ticker"]
        rows.append({
            "fecha": r["_id"]["fecha"],
            "curva": ticker_curva[ticker],
            "money": r["money"],
        })
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.groupby(["fecha", "curva"], as_index=False)["money"].sum()
    df["money_mm"] = df["money"] / 1_000_000
    return df.sort_values("fecha")


def _render_volumenes():
    import altair as alt

    with st.spinner("Cargando volúmenes..."):
        df = _cargar_volumenes_diarios()

    if df.empty:
        st.info("Sin datos de volumen en TimeSales para los instrumentos de curvas.")
        return

    fechas = sorted(df["fecha"].unique())

    if len(fechas) < 2:
        st.info("Necesitás al menos 2 días de datos.")
        return

    # ── Filtros ───────────────────────────────────────────────────
    col1, col2 = st.columns([2, 3])
    with col1:
        curvas_disp = sorted(df["curva"].unique())
        curvas_sel = st.multiselect(
            "Curvas", curvas_disp, default=curvas_disp, key="vol_curvas"
        )
    with col2:
        fecha_desde, fecha_hasta = st.select_slider(
            "Período",
            options=fechas,
            value=(fechas[0], fechas[-1]),
            key="vol_rango",
        )

    if not curvas_sel:
        st.info("Seleccioná al menos una curva.")
        return

    df_rango = df[
        (df["fecha"] >= fecha_desde) &
        (df["fecha"] <= fecha_hasta) &
        (df["curva"].isin(curvas_sel))
    ].copy()
    fechas_rango = sorted(df_rango["fecha"].unique())

    if df_rango.empty:
        st.info("Sin datos en el rango seleccionado.")
        return

    # ── Gráfico barras apiladas ───────────────────────────────────
    chart = (
        alt.Chart(df_rango)
        .mark_bar()
        .encode(
            x=alt.X("fecha:O", title="Fecha", sort=fechas_rango,
                    axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("money_mm:Q", title="Volumen (MM ARS)", stack=True,
                    axis=alt.Axis(format=",.0f")),
            color=alt.Color("curva:N", title="Curva",
                            legend=alt.Legend(orient="top")),
            tooltip=[
                alt.Tooltip("fecha:O", title="Fecha"),
                alt.Tooltip("curva:N", title="Curva"),
                alt.Tooltip("money_mm:Q", format=",.0f", title="Volumen (MM ARS)"),
            ],
        )
        .properties(height=420)
    )

    st.altair_chart(chart, use_container_width=True)

    # ── Tabla resumen del rango seleccionado ──────────────────────
    resumen = (
        df_rango.groupby("curva")["money_mm"]
        .sum()
        .reset_index()
        .rename(columns={"curva": "Curva", "money_mm": "Total (MM ARS)"})
        .sort_values("Total (MM ARS)", ascending=False)
        .reset_index(drop=True)
    )
    resumen["Total (MM ARS)"] = resumen["Total (MM ARS)"].apply(lambda v: f"{v:,.0f}")
    st.dataframe(resumen, hide_index=True, use_container_width=True,
                 height=df_height(len(resumen)))


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_precios_diarios_curva(curva):
    """
    Último precio por ticker por día para todos los instrumentos de una curva.
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


def _render_retorno_total(key_prefix="rt"):
    import altair as alt

    db = get_db()
    curvas = sorted(db["Curvas"].distinct("curva"))
    if not curvas:
        st.info("Sin curvas configuradas en Trading.Curvas.")
        return

    curva_sel = st.selectbox("Curva", curvas, key=f"{key_prefix}_curva")

    with st.spinner("Cargando precios históricos..."):
        df_raw = _cargar_precios_diarios_curva(curva_sel)

    if df_raw.empty:
        st.info("Sin datos de precios en TimeSales para esta curva.")
        return

    fechas_ord = sorted(df_raw["fecha"].unique())

    if len(fechas_ord) < 2:
        st.info("Necesitás al menos 2 días de datos para calcular retorno.")
        return

    # ── Selector de rango (desde / hasta) ────────────────────────
    st.caption("El retorno parte de 0% en la fecha de inicio. Ajustá ambos extremos del slider.")
    fecha_base, fecha_fin = st.select_slider(
        "Período (desde → hasta)",
        options=fechas_ord,
        value=(fechas_ord[0], fechas_ord[-1]),
        key=f"{key_prefix}_rango",
    )

    # ── Calcular retorno acumulado en el rango ────────────────────
    df_pivot = df_raw.pivot_table(index="fecha", columns="ticker", values="price", aggfunc="last")
    df_pivot = df_pivot.sort_index()

    df_desde = df_pivot.loc[(df_pivot.index >= fecha_base) & (df_pivot.index <= fecha_fin)].copy()
    base = df_desde.iloc[0]
    df_retorno = (df_desde.div(base) - 1) * 100

    df_long = (
        df_retorno
        .reset_index()
        .melt(id_vars="fecha", var_name="Ticker", value_name="Retorno (%)")
        .dropna(subset=["Retorno (%)"])
    )
    fechas_rango = sorted(df_long["fecha"].unique())

    # ── Gráfico de líneas ─────────────────────────────────────────
    regla_cero = (
        alt.Chart(pd.DataFrame({"y": [0]}))
        .mark_rule(color="#555", strokeWidth=1)
        .encode(y=alt.Y("y:Q"))
    )
    lineas = (
        alt.Chart(df_long)
        .mark_line(point=alt.OverlayMarkDef(size=50))
        .encode(
            x=alt.X("fecha:O", title="Fecha", sort=fechas_rango,
                    axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("Retorno (%):Q", title="Retorno acumulado (%)",
                    axis=alt.Axis(format=".2f")),
            color=alt.Color("Ticker:N", legend=alt.Legend(title="Instrumento")),
            tooltip=[
                alt.Tooltip("fecha:O", title="Fecha"),
                alt.Tooltip("Ticker:N"),
                alt.Tooltip("Retorno (%):Q", format=".2f", title="Retorno (%)"),
            ],
        )
    )
    st.altair_chart(
        (regla_cero + lineas).properties(height=450),
        use_container_width=True,
    )

    # ── Tabla: retorno al último día del rango ────────────────────
    fecha_ultimo = fechas_rango[-1]
    st.markdown(f"#### Retorno acumulado: {fecha_base} → {fecha_ultimo}")

    df_tabla = (
        df_long[df_long["fecha"] == fecha_ultimo][["Ticker", "Retorno (%)"]]
        .copy()
        .sort_values("Retorno (%)", ascending=False)
        .reset_index(drop=True)
    )
    precios_base  = df_desde.iloc[0]
    precios_final = df_desde.loc[fecha_ultimo] if fecha_ultimo in df_desde.index else pd.Series(dtype=float)
    df_tabla["Precio base"]  = df_tabla["Ticker"].map(precios_base).round(4)
    df_tabla["Precio final"] = df_tabla["Ticker"].map(precios_final).round(4)
    df_tabla["Retorno (%)"]  = df_tabla["Retorno (%)"].round(2).astype(str) + "%"

    st.dataframe(df_tabla, hide_index=True, use_container_width=True,
                 height=df_height(len(df_tabla)))


def vista_retorno_total():
    st.markdown("## ACAQuant | Retorno Total")
    _render_retorno_total(key_prefix="rt_page")


# Ruteo: solo se llama el fragmento activo.
if vista == "Opciones":
    vista_opciones()
elif vista == "Mercado":
    vista_mercado()
elif vista == "Portfolios":
    vista_portfolios()
elif vista == "Operaciones":
    vista_operaciones()
elif vista == "AuM":
    vista_aum()
elif vista == "Manager":
    if is_manager_allowed():
        vista_data_manager()
    else:
        st.error("Acceso denegado.")
