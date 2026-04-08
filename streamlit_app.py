import streamlit as st
import pandas as pd
import altair as alt
import re
import requests
from datetime import datetime, timedelta
from mongo_manager import get_mongo_client
from tickers import MERV_TICKERS as TICKERS
from Opciones.calculos_cuantitativos import bs_price as _bs_price
import config

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

def short_name(ticker):
    parts = ticker.split(" - ")
    return parts[2] if len(parts) >= 3 else ticker


# ==========================================
# CONEXIÓN A MONGO (cached, una sola vez)
# ==========================================
@st.cache_resource(ttl=3600)
def get_db():
    return get_mongo_client()["Trading"]

@st.cache_resource(ttl=3600)
def get_db_opciones():
    return get_mongo_client()["Opciones"]

@st.cache_resource(ttl=3600)
def get_meta_col():
    return get_mongo_client()["Opciones"]["Metadata"]

@st.cache_resource(ttl=3600)
def get_db_valuaciones():
    return get_mongo_client()["Valuaciones"]


# ==========================================
# SIDEBAR - NAVEGACIÓN
# ==========================================
with st.sidebar:
    st.image("images/logo-header.png", use_container_width=True)
    st.markdown("---")
    vista = st.radio(
        "Vista",
        ["Mercado", "Opciones", "Portfolios", "Operaciones", "AuM"],
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
        st.info("Sin trades recientes.")
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
        buy        = m.get("buy_money",   0) or 0
        sell       = m.get("sell_money",  0) or 0
        imb        = m.get("imbalance",   0) or 0
        last_price = m.get("last_price",  0) or 0
        open_price = m.get("open_price",  0) or 0
        closing    = m.get("closing_price", 0) or 0
        vwap       = m.get("vwap",        0) or 0
        if total == 0:
            continue
        intraday  = (last_price / open_price - 1) if open_price > 0 and last_price > 0 else None
        vs_cierre = (last_price / closing - 1) if closing > 0 and last_price > 0 else None

        enc = enriched.get(ticker, {})
        tea     = enc.get("TEA")
        dur     = enc.get("duration")

        rows.append({
            "Ticker":    short_name(ticker),
            "Last":      last_price   if last_price  > 0 else None,
            "TEA":       tea,
            "Duration":  dur,
            "Total $":   fmt_money(total),
            "Buy $":     fmt_money(buy),
            "Sell $":    fmt_money(sell),
            "Open":      open_price   if open_price  > 0 else None,
            "Cierre":    closing      if closing      > 0 else None,
            "VWAP":      vwap         if vwap         > 0 else None,
            "Intraday":  intraday,
            "1D%":       vs_cierre,
            "Imbalance": imb,
        })
    if not rows:
        st.info("Todos los tickers sin volumen aún.")
        return
    df = pd.DataFrame(rows)

    def pct_color(v):
        if pd.isna(v): return ""
        return "color: #00cc66; font-weight: bold" if v >= 0 else "color: #ff4444; font-weight: bold"

    fmt = {
        "Last":      lambda v: f"{v:,.2f}" if pd.notna(v) else "-",
        "TEA":       lambda v: f"{v:.2%}" if pd.notna(v) else "-",
        "Duration":  lambda v: f"{v:.2f}" if pd.notna(v) else "-",
        "Open":      lambda v: f"{v:,.2f}" if pd.notna(v) else "-",
        "Cierre":    lambda v: f"{v:,.2f}" if pd.notna(v) else "-",
        "VWAP":      lambda v: f"{v:,.2f}" if pd.notna(v) else "-",
        "Intraday":  lambda v: f"{v:+.2%}" if pd.notna(v) else "-",
        "1D%":       lambda v: f"{v:+.2%}" if pd.notna(v) else "-",
        "Imbalance": "{:.2%}",
    }

    styler = (
        df.style
        .map(pct_color, subset=["Intraday", "1D%"])
        .map(lambda v: (
            "color: #00cc66; font-weight: bold" if v > 0.05 else
            "color: #ff4444; font-weight: bold" if v < -0.05 else
            "color: #aaa"
        ), subset=["Imbalance"])
        .format(fmt)
    )
    st.caption("RESUMEN DE MERCADO")
    st.dataframe(styler, hide_index=True, use_container_width=True, height=df_height(len(df), max_h=900))


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

    col_ticker, _ = st.columns([1, 3])
    with col_ticker:
        if "selected_ticker" not in st.session_state:
            st.session_state.selected_ticker = TICKERS[0]

        options_short = [short_name(t) for t in TICKERS]
        current_idx   = TICKERS.index(st.session_state.selected_ticker)

        selected_short = st.selectbox(
            "Ticker", options_short,
            index=current_idx,
            label_visibility="collapsed",
        )
        st.session_state.selected_ticker = TICKERS[options_short.index(selected_short)]

    ticker = st.session_state.selected_ticker
    snap   = db["MarketSnapshot"].find_one({"ticker": ticker})

    if not snap:
        st.warning(f"Sin datos para {ticker}. ¿El motor está corriendo?")
        return

    last_update_badge(snap.get("updated_at"))
    st.divider()

    book          = snap.get("book", {"bids": [], "offers": []})
    metrics       = snap.get("metrics", {})
    hourly_stats  = snap.get("hourly_stats", {})
    recent_trades = snap.get("recent_trades", [])
    top_trades    = snap.get("top_trades", [])

    # Altura de col_left: depth (5r) + quant (10r) + captions/espaciado
    _TAPE_HEIGHT = df_height(5) + df_height(10) + 80  # ≈ 681px

    # Fila 1: depth+quant | tape | whales
    col_left, col_center, col_right = st.columns([1, 1, 1])
    with col_left:
        render_depth(book)
        st.write("")
        render_quant(metrics)
    with col_center:
        render_tape(recent_trades, height=_TAPE_HEIGHT)
    with col_right:
        render_whales(top_trades)

    # Fila 2: HOURLY VOL | LAST MINUTES — misma fila = misma altura de arranque
    col_hourly, col_chart = st.columns([1, 2])
    with col_hourly:
        render_hourly(hourly_stats)
    with col_chart:
        trade_prices = [
            {
                "Hora": t["timestamp"].strftime("%H:%M") if hasattr(t.get("timestamp"), "strftime") else "",
                "Precio": t.get("price", 0),
            }
            for t in sorted(recent_trades, key=lambda x: x.get("timestamp", datetime.min))
            if t.get("price", 0) > 0
        ]
        if trade_prices:
            chart_df = pd.DataFrame(trade_prices)
            chart = (
                alt.Chart(chart_df)
                .mark_line(point=True)
                .encode(
                    x=alt.X("Hora:O", title=None, sort=None),
                    y=alt.Y("Precio:Q", scale=alt.Scale(zero=False), title=None),
                )
                .properties(height=_HOURLY_HEIGHT)
                .interactive()
            )
            st.caption("LAST MINUTES")
            st.altair_chart(chart, use_container_width=True)


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
    docs = list(get_mongo_client()["Opciones"]["Data"].aggregate(pipeline))
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
    """Convierte lista de pares a DataFrame formateado para st.dataframe."""
    rows = []
    for p in pares:
        bkv = p.get("breakeven_mensual")
        tem = p.get("tem_lecap")
        par = p.get("paridad_cer")
        rows.append({
            "#":                  p["n"],
            "Lecap":              p["lecap"],
            "CER":                p["cer"],
            "Plazo":              _fmt_plazo(p["fecha_vencimiento"]),
            "Días":               p["dias"],
            "TEM Lecap":          f"{tem * 100:.2f}%" if tem is not None else "—",
            "Paridad CER":        f"{par:.1f}%" if par is not None else "—",
            "Breakeven mensual":  f"{bkv * 100:.2f}%" if bkv is not None else "—",
        })
    return pd.DataFrame(rows)


def _render_breakevens(db):
    from datetime import date as _date

    tab_live, tab_hist = st.tabs(["Tiempo Real", "Histórico"])

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
                st.dataframe(
                    _tabla_breakevens(pares),
                    hide_index=True,
                    use_container_width=True,
                    height=df_height(len(pares)),
                )
            else:
                st.info("Motor activo pero sin pares calculados aún.")

    with tab_hist:
        fechas = sorted(
            [d["fecha"] for d in db["BreakevensHistorico"].find({}, {"fecha": 1, "_id": 0})],
            reverse=True,
        )
        if not fechas:
            st.info("Sin historial disponible aún.")
            return

        fecha_sel = st.select_slider("Fecha", options=fechas, key="bkv_fecha_slider")
        doc_hist = db["BreakevensHistorico"].find_one({"fecha": fecha_sel})
        if doc_hist:
            pares = doc_hist.get("pares", [])
            if pares:
                st.dataframe(
                    _tabla_breakevens(pares),
                    hide_index=True,
                    use_container_width=True,
                    height=df_height(len(pares)),
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

            if all_snaps:
                ultimo_ts = max(
                    (s.get("updated_at") for s in all_snaps if s.get("updated_at")),
                    default=None
                )
                last_update_badge(ultimo_ts)
            st.caption("Vista con actualización automática cada 30 segundos.")

            st.divider()

            curvas_tickers = [d["ticker"] for d in db["Curvas"].find({}, {"ticker": 1})]
            enriched = {}
            for ticker in curvas_tickers:
                doc = db["TimeSales"].find_one(
                    {"ticker": ticker, "duration": {"$exists": True}},
                    sort=[("timestamp", -1)]
                )
                if doc:
                    enriched[ticker] = doc

            all_snaps.sort(
                key=lambda s: s.get("metrics", {}).get("total_money", 0) or 0,
                reverse=True
            )
            render_mercado_table(all_snaps, enriched)

        _tab_mercado_live()

    with tab_libro:
        vista_libro()

    with tab_curvas:
        _render_curva_rendimiento(db)

    with tab_breakevens:
        _render_breakevens(db)

    with tab_forwards:
        _render_forwards(db, key_prefix="fwd_mercado")

    with tab_retorno:
        _render_retorno_total(key_prefix="rt_mercado")

    with tab_vol:
        _render_volumenes()


@st.cache_data(ttl=300, show_spinner=False)
def _get_dolar_oficial():
    """Último valor de Trading.DOLAR (tipo de cambio A3500 desde BCRA)."""
    doc = get_db()["DOLAR"].find_one(sort=[("fecha", -1)])
    return float(doc["valor"]) if doc else None


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

    # ── tabs por cuenta ───────────────────────────────────────────────────
    cuentas = sorted(df["id_cuenta"].dropna().unique().tolist())
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
    return get_mongo_client()["CashFlow"]


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


def vista_operaciones():
    st.markdown("## ACAQuant | Cash Flow")

    df = _cargar_movimientos()
    if df.empty:
        st.warning("Sin datos. Ejecutá `main_cashflow.py` para cargar el historial.")
        return

    min_date = df["fecha"].min().date()
    max_date = df["fecha"].max().date()

    COLOR_ARS = "#094293"
    COLOR_USD = "#00cc66"

    # ── Slider (fila completa) ────────────────────────────────────────────────
    rango = st.slider(
        "Rango de fechas",
        min_value=min_date,
        max_value=max_date,
        value=(min_date, max_date),
        format="DD/MM/YY",
        key="ops_rango",
    )

    # ── Filtros en una fila ───────────────────────────────────────────────────
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

    # ── Filtro accionistas ────────────────────────────────────────────────────
    acc_map = _cargar_accionistas()   # {cuenta: accionista}
    acc_nombres = sorted(set(acc_map.values()))

    fa_col, fb_col, fc_col = st.columns([2, 3, 2])
    with fa_col:
        filtro_acc = st.selectbox(
            "Cuentas",
            ["Todas", "Sin accionistas", "Solo accionistas"],
            key="ops_filtro_acc",
            label_visibility="collapsed",
        )
    with fb_col:
        if filtro_acc == "Solo accionistas":
            acc_sel = st.multiselect(
                "Accionista", acc_nombres, default=acc_nombres, key="ops_acc_sel"
            )
        else:
            acc_sel = acc_nombres
    with fc_col:
        cuentas_disponibles = ["Todas"] + sorted(df["cuenta"].dropna().unique().tolist())
        cuenta_sel = st.selectbox(
            "Cuenta", cuentas_disponibles, key="ops_cuenta",
            label_visibility="collapsed",
        )

    # ── Filtrar ───────────────────────────────────────────────────────────────
    df_f = df[(df["fecha"].dt.date >= rango[0]) & (df["fecha"].dt.date <= rango[1])].copy()
    monedas_sel = (["ARS"] if show_ars else []) + (["USD"] if show_usd else [])
    df_f = df_f[df_f["unidad"].isin(monedas_sel)].copy()

    # Aplicar filtro de cuenta
    if cuenta_sel != "Todas":
        df_f = df_f[df_f["cuenta"] == cuenta_sel].copy()

    # Aplicar filtro de accionistas
    df_f["_accionista"] = df_f["cuenta"].map(acc_map)
    if filtro_acc == "Sin accionistas":
        df_f = df_f[df_f["_accionista"].isna()].copy()
    elif filtro_acc == "Solo accionistas":
        df_f = df_f[df_f["_accionista"].isin(acc_sel)].copy()

    if df_f.empty:
        st.info("Sin datos para el rango/moneda seleccionados.")
        return

    # ── Agrupar ───────────────────────────────────────────────────────────────
    # Clave string garantiza un único valor por periodo/moneda.
    # Encoding ordinal en Altair → una barra exacta por etiqueta, sin interpolación.
    if granularity == "Mensual":
        df_f["_key"] = df_f["fecha"].dt.strftime("%Y-%m")
        df_agg = df_f.groupby(["_key", "unidad"], as_index=False)["total"].sum()
        df_agg = df_agg.sort_values("_key")
        df_agg["label"] = pd.to_datetime(df_agg["_key"] + "-01").dt.strftime("%b %Y")
    else:
        df_f["_key"] = df_f["fecha"].dt.strftime("%Y-%m-%d")
        df_agg = df_f.groupby(["_key", "unidad"], as_index=False)["total"].sum()
        df_agg = df_agg.sort_values("_key")
        df_agg["label"] = pd.to_datetime(df_agg["_key"]).dt.strftime("%d/%m/%y")

    # Orden cronológico explícito para el eje ordinal
    x_order = list(dict.fromkeys(df_agg["label"].tolist()))
    df_agg = df_agg.drop(columns="_key")

    # ── Gráfico — un chart independiente por moneda ──────────────────────────
    def make_chart(moneda, color):
        data = df_agg[df_agg["unidad"] == moneda].copy()
        if data.empty:
            return None
        # Normalizar eje Y para evitar notación "G"
        max_abs = data["total"].abs().max()
        if max_abs >= 1e9:
            data["valor"] = data["total"] / 1e9
            y_title = f"Billones {moneda}"
            y_fmt   = ",.2f"
        elif max_abs >= 1e6:
            data["valor"] = data["total"] / 1e6
            y_title = f"Millones {moneda}"
            y_fmt   = ",.1f"
        elif max_abs >= 1e3:
            data["valor"] = data["total"] / 1e3
            y_title = f"Miles {moneda}"
            y_fmt   = ",.1f"
        else:
            data["valor"] = data["total"]
            y_title = moneda
            y_fmt   = ",.0f"

        x_enc = alt.X("label:O", sort=x_order, axis=alt.Axis(labelAngle=-45, title=None))
        y_enc = alt.Y("valor:Q", axis=alt.Axis(title=y_title, titleColor=color, format=y_fmt))
        tip   = [alt.Tooltip("label:O", title="Fecha"),
                 alt.Tooltip("valor:Q", title=y_title, format=y_fmt)]

        bars = (
            alt.Chart(data)
            .mark_bar(color=color, opacity=0.85,
                      cornerRadiusTopLeft=2, cornerRadiusTopRight=2)
            .encode(x=x_enc, y=y_enc, tooltip=tip)
        )

        if granularity != "Mensual":
            return bars.properties(height=220)

        # Etiquetas dinámicas solo en modo mensual
        max_val  = data["valor"].abs().max()
        umbral   = max_val * 0.20   # barra "grande" si supera el 20% del máximo
        padding  = max_val * 0.04   # desplazamiento para texto exterior

        data["mid"]      = data["valor"] / 2
        data["exterior"] = data["valor"].apply(
            lambda v: v + padding if v >= 0 else v - padding
        )

        grandes  = data[data["valor"].abs() >= umbral]
        chicas   = data[data["valor"].abs() <  umbral]

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

        return (
            alt.layer(bars, txt_inside, txt_outside)
            .properties(height=260)
        )

    for moneda, color in [("ARS", COLOR_ARS), ("USD", COLOR_USD)]:
        if moneda not in monedas_sel:
            continue
        chart = make_chart(moneda, color)
        if chart:
            st.altair_chart(chart, use_container_width=True)

    # ── Tarjetas de resumen ───────────────────────────────────────────────────
    tarjeta_cols = st.columns(len(monedas_sel))
    for i, moneda in enumerate(monedas_sel):
        color = COLOR_ARS if moneda == "ARS" else COLOR_USD
        sub = df_f[df_f["unidad"] == moneda]
        entradas = sub[sub["total"] > 0]["total"].sum()
        salidas  = sub[sub["total"] < 0]["total"].sum()
        neto     = entradas + salidas
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
                    .properties(height=260)
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
                    .agg(valuacion=("valuacion", "sum"), pago_final=("pago_final", "sum"))
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
                st.markdown(
                    f"<div style='display:flex;gap:40px;margin-bottom:8px'>"
                    f"<div><span style='font-size:11px;color:#888'>Valuación actual</span><br>"
                    f"<span style='font-size:17px;font-weight:600'>${total_val_tf:,.0f}</span></div>"
                    f"<div><span style='font-size:11px;color:#888'>Cobro proyectado</span><br>"
                    f"<span style='font-size:17px;font-weight:600'>${total_cobro:,.0f}</span></div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                h_tbl = 38 + 35 * len(tbl)

                # ── fila 1: tabla tickers | tabla cuentas (mismo tamaño) ──
                col_tbl, col_det = st.columns([2, 3])

                with col_tbl:
                    tbl_display = tbl[["ticker_corto", "fecha_venc", "valuacion"]].copy()
                    tbl_display["valuacion"] = tbl_display["valuacion"].apply(lambda v: f"${v:,.0f}")
                    tbl_display.rename(columns={
                        "ticker_corto": "Ticker",
                        "fecha_venc":   "Vencimiento",
                        "valuacion":    "Valuación",
                    }, inplace=True)
                    ev_tf = st.dataframe(
                        tbl_display, hide_index=True, use_container_width=True,
                        height=h_tbl, on_select="rerun", selection_mode="single-row",
                        key="tf_tabla",
                        column_config={
                            "Ticker":      st.column_config.TextColumn(width="small"),
                            "Vencimiento": st.column_config.TextColumn(width="small"),
                            "Valuación":   st.column_config.TextColumn(width="small"),
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
                        .groupby("cuenta", as_index=False)["valuacion"]
                        .sum()
                        .sort_values("valuacion", ascending=False)
                        .reset_index(drop=True)
                    )
                    df_det["Valuación"] = df_det["valuacion"].apply(lambda v: f"${v:,.0f}")
                    st.dataframe(
                        df_det[["cuenta", "Valuación"]].rename(columns={"cuenta": "Cuenta"}),
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
                    .agg(valuacion=("valuacion", "sum"))
                    .sort_values("fecha_venc")
                    .reset_index(drop=True)
                )

                total_val_cer = tbl_cer["valuacion"].sum()
                st.markdown(
                    f"<div style='margin-bottom:8px'>"
                    f"<span style='font-size:11px;color:#888'>Valuación actual</span><br>"
                    f"<span style='font-size:17px;font-weight:600'>${total_val_cer:,.0f}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                h_cer = 38 + 35 * len(tbl_cer)
                col_tbl_c, col_det_c = st.columns([2, 3])

                with col_tbl_c:
                    tbl_cer_disp = tbl_cer[["ticker_corto", "fecha_venc", "valuacion"]].copy()
                    tbl_cer_disp["valuacion"] = tbl_cer_disp["valuacion"].apply(lambda v: f"${v:,.0f}")
                    tbl_cer_disp.rename(columns={
                        "ticker_corto": "Ticker",
                        "fecha_venc":   "Vencimiento",
                        "valuacion":    "Valuación",
                    }, inplace=True)
                    ev_cer = st.dataframe(
                        tbl_cer_disp, hide_index=True, use_container_width=True,
                        height=h_cer, on_select="rerun", selection_mode="single-row",
                        key="cer_tabla",
                        column_config={
                            "Ticker":      st.column_config.TextColumn(width="small"),
                            "Vencimiento": st.column_config.TextColumn(width="small"),
                            "Valuación":   st.column_config.TextColumn(width="small"),
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
                        .groupby("cuenta", as_index=False)["valuacion"]
                        .sum()
                        .sort_values("valuacion", ascending=False)
                        .reset_index(drop=True)
                    )
                    df_det_c["Valuación"] = df_det_c["valuacion"].apply(lambda v: f"${v:,.0f}")
                    st.dataframe(
                        df_det_c[["cuenta", "Valuación"]].rename(columns={"cuenta": "Cuenta"}),
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


# ==========================================
# ONs
# ==========================================
@st.fragment(run_every=30)
def vista_ons():
    db_val = get_db_valuaciones()
    db_trading = get_db()

    # ── TC Oficial editable ────────────────────────────────────────────────
    tc_cfg = db_val["Dolar"].find_one({"type": "config_on"})
    tc_actual = float(tc_cfg.get("tc_oficial", 0)) if tc_cfg else 0.0
    if "tc_on_display" not in st.session_state:
        st.session_state["tc_on_display"] = tc_actual

    header_col, tc_col = st.columns([3, 1])
    with header_col:
        st.markdown("## ACAQuant | Yield Screener ONs")
    with tc_col:
        nuevo_tc = st.number_input(
            "TC Oficial", min_value=0.0, max_value=10_000_000.0,
            value=st.session_state["tc_on_display"],
            step=1.0, format="%.2f",
            key="tc_on_input", label_visibility="collapsed",
            placeholder="TC Oficial ARS/USD",
        )
        if abs(nuevo_tc - st.session_state["tc_on_display"]) > 1e-4:
            db_val["Dolar"].update_one(
                {"type": "config_on"},
                {"$set": {"tc_oficial": nuevo_tc, "updated_at": datetime.utcnow()}},
                upsert=True
            )
            st.session_state["tc_on_display"] = nuevo_tc
            st.toast(f"TC Oficial actualizado a ${nuevo_tc:,.2f}", icon="✅")

    docs = list(db_trading["ONSnapshot"].find({}, {"_id": 0}))
    if not docs:
        st.warning("Sin datos. ¿Está corriendo `main_on.py` en el servidor?")
        return

    df = pd.DataFrame(docs)

    cols_num = ["tir_bid", "tir_off", "px_bid", "px_off", "vol_bid", "vol_off", "mep_vivo"]
    for c in cols_num:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    if "timestamp" in df.columns:
        ts = pd.to_datetime(df["timestamp"], errors="coerce").dropna()
        if not ts.empty:
            st.caption(f"Último snapshot: {ts.max().strftime('%H:%M:%S')} UTC")

    mep_ref = df["mep_vivo"].dropna().iloc[0] if "mep_vivo" in df.columns and not df["mep_vivo"].dropna().empty else None
    if mep_ref:
        st.caption(f"MEP ref (live): ${mep_ref:,.2f}  |  TC Oficial: ${nuevo_tc:,.2f}")

    if "vence" in df.columns:
        df["anio"] = df["vence"].str.extract(r"/(\d{4})$").squeeze()

    fcol1, fcol2 = st.columns(2)
    with fcol1:
        emisores = ["Todos"] + sorted(df["emisor"].dropna().unique().tolist())
        emisor_sel = st.selectbox("Emisor", emisores, key="on_emisor")
    with fcol2:
        anios = ["Todos"] + sorted(df["anio"].dropna().unique().tolist()) if "anio" in df.columns else ["Todos"]
        anio_sel = st.selectbox("Vencimiento (año)", anios, key="on_anio")

    if emisor_sel != "Todos":
        df = df[df["emisor"] == emisor_sel]
    if anio_sel != "Todos" and "anio" in df.columns:
        df = df[df["anio"] == anio_sel]

    df = df.sort_values("tir_off", ascending=False, na_position="last")

    def fmt_tir(v):
        return f"{v:.2f}%" if pd.notna(v) else "---"

    def fmt_px(v):
        return f"${v:,.2f}" if pd.notna(v) else "---"

    def fmt_vol_on(v):
        if pd.isna(v) or v == 0: return "-"
        if v >= 1_000_000: return f"${v/1_000_000:.1f}M"
        if v >= 1_000: return f"${v/1_000:.0f}K"
        return f"${v:.0f}"

    display_cols = {
        "asset":    "Bono",
        "emisor":   "Emisor",
        "vence":    "Venc.",
        "moneda":   "Mon.",
        "duration": "Duration",
        "vol_bid":  "Vol Bid",
        "px_bid":   "Bid",
        "tir_bid":  "TIR Bid",
        "tir_off":  "TIR Off",
        "px_off":   "Offer",
        "vol_off":  "Vol Off",
    }

    df_show = df[[c for c in display_cols if c in df.columns]].copy()
    df_show = df_show.rename(columns=display_cols)

    if "Duration" in df_show.columns:
        df_show["Duration"] = df["duration"].apply(
            lambda v: f"{v:.2f}a" if pd.notna(v) else "---"
        )
    if "Vol Bid" in df_show.columns:
        df_show["Vol Bid"] = df["vol_bid"].apply(fmt_vol_on)
    if "Vol Off" in df_show.columns:
        df_show["Vol Off"] = df["vol_off"].apply(fmt_vol_on)
    if "Bid" in df_show.columns:
        df_show["Bid"] = df["px_bid"].apply(fmt_px)
    if "Offer" in df_show.columns:
        df_show["Offer"] = df["px_off"].apply(fmt_px)
    if "TIR Bid" in df_show.columns:
        df_show["TIR Bid"] = df["tir_bid"].apply(fmt_tir)
    if "TIR Off" in df_show.columns:
        df_show["TIR Off"] = df["tir_off"].apply(fmt_tir)

    st.dataframe(df_show, use_container_width=True, hide_index=True)


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

    st.caption("MATRIZ DE TASAS FORWARD")

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
        # Cargar todos los docs históricos de esta curva
        docs_hist = list(db["ForwardsHistorico"].find(
            {"curva": curva_sel},
            {"fecha": 1, "matrix": 1, "_id": 0}
        ))
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
