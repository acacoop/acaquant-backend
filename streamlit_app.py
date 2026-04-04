import streamlit as st
import pandas as pd
import altair as alt
import re
import requests
from datetime import datetime, timedelta
from mongo_manager import get_mongo_client
from tickers import MERV_TICKERS as TICKERS
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
        ["Libro", "Mercado", "Forwards", "Opciones", "Estrategias Opciones", "Carteras", "Operaciones", "AuM", "ONs"],
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
        tem     = enc.get("TEM")
        dur     = enc.get("duration")
        paridad = enc.get("paridad")

        rows.append({
            "Ticker":    short_name(ticker),
            "Last":      last_price   if last_price  > 0 else None,
            "TEA":       tea,
            "TEM":       tem,
            "Duration":  dur,
            "Paridad":   paridad,
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
        "TEM":       lambda v: f"{v:.2%}" if pd.notna(v) else "-",
        "Duration":  lambda v: f"{v:.2f}" if pd.notna(v) else "-",
        "Paridad":   lambda v: f"{v:.2f}%" if pd.notna(v) else "-",
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
        .map(lambda v: "color: #00cc66; font-weight: bold" if pd.notna(v) else "", subset=["C Bid", "P Bid"])
        .map(lambda v: "color: #ff4444; font-weight: bold" if pd.notna(v) else "", subset=["C Offer", "P Offer"])
        .map(lambda v: "color: #f0c040; font-weight: bold", subset=["STRIKE"])
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
    # Greeks heatmap: color via applymap (sin matplotlib)
    def _iv_color(v):
        try:
            val = float(str(v).replace("%", ""))
            if val < 50:   return "color: #4caf50"
            if val < 80:   return "color: #ff9800"
            return "color: #f44336"
        except Exception:
            return ""

    def _delta_color(v):
        try:
            val = float(v)
            if val > 0.6:  return "color: #4caf50"
            if val > 0.3:  return "color: #ff9800"
            if val < -0.6: return "color: #f44336"
            if val < -0.3: return "color: #ff9800"
            return ""
        except Exception:
            return ""

    _map = "map" if hasattr(styler, "map") else "applymap"
    for col in ["C IV %", "P IV %"]:
        if col in df.columns:
            styler = getattr(styler, _map)(_iv_color, subset=[col])
    for col in ["C Delta", "P Delta"]:
        if col in df.columns:
            styler = getattr(styler, _map)(_delta_color, subset=[col])

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
        t.append(("Bull Call Spread", f"Bull Call Spread +{n}", [(0,'CALL','buy',1), (+n,'CALL','sell',1)]))
    for n in range(1, 7):
        t.append(("Bear Put Spread",  f"Bear Put Spread  -{n}", [(0,'PUT','buy',1), (-n,'PUT','sell',1)]))
    t.append(("Straddle / Strangle", "Straddle ATM", [(0,'CALL','buy',1), (0,'PUT','buy',1)]))
    for n in range(1, 6):
        t.append(("Straddle / Strangle", f"Strangle         {n}w", [(+n,'CALL','buy',1), (-n,'PUT','buy',1)]))
    for n in range(1, 6):
        t.append(("Ratio Call 1×2",  f"Ratio Call 1×2   +{n}", [(0,'CALL','buy',1), (+n,'CALL','sell',2)]))
    for n in range(1, 6):
        t.append(("Ratio Put 1×2",   f"Ratio Put  1×2   -{n}", [(0,'PUT','buy',1), (-n,'PUT','sell',2)]))
    for n in range(1, 6):
        t.append(("Call Backspread",  f"Call Backspread  +{n}", [(0,'CALL','sell',1), (+n,'CALL','buy',2)]))
    for n in range(1, 6):
        t.append(("Put Backspread",   f"Put Backspread   -{n}", [(0,'PUT','sell',1), (-n,'PUT','buy',2)]))
    t.append(("Iron Condor", "Iron Condor  1|2", [(-2,'PUT','buy',1),(-1,'PUT','sell',1),(+1,'CALL','sell',1),(+2,'CALL','buy',1)]))
    t.append(("Iron Condor", "Iron Condor  2|3", [(-3,'PUT','buy',1),(-2,'PUT','sell',1),(+2,'CALL','sell',1),(+3,'CALL','buy',1)]))
    t.append(("Iron Condor", "Iron Condor  1|3", [(-3,'PUT','buy',1),(-1,'PUT','sell',1),(+1,'CALL','sell',1),(+3,'CALL','buy',1)]))
    t.append(("Iron Condor", "Iron Condor  1|4", [(-4,'PUT','buy',1),(-1,'PUT','sell',1),(+1,'CALL','sell',1),(+4,'CALL','buy',1)]))
    t.append(("Iron Condor", "Iron Condor  2|4", [(-4,'PUT','buy',1),(-2,'PUT','sell',1),(+2,'CALL','sell',1),(+4,'CALL','buy',1)]))
    t.append(("Short Vol", "Short Straddle",     [(0,'CALL','sell',1), (0,'PUT','sell',1)]))
    for n in range(1, 5):
        t.append(("Short Vol", f"Short Strangle   {n}w", [(+n,'CALL','sell',1), (-n,'PUT','sell',1)]))
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

def _calcular_estrategias(por_strike, liquid_strikes, center_idx, spot, categoria_sel="Todas"):
    """Construye filas de la tabla y la lista de patas resueltas (symbol, K, side, qty, px)."""
    def get_px(d, side):
        if not d: return 0
        offer = d.get('offer', 0) or 0
        bid   = d.get('bid',   0) or 0
        last  = d.get('last',  0) or 0
        return (offer if side == 'buy' else bid) if (offer > 0 and bid > 0) else last

    rows, resolved_legs_list = [], []

    for cat, name, legs in STRATEGY_TEMPLATES:
        if categoria_sel != "Todas" and cat != categoria_sel:
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
            resolved_legs.append({
                'symbol': (d.get('symbol') or '') if d else '',
                'K': K, 'tipo': tipo, 'side': side, 'qty': qty, 'px': px,
            })

        rows.append({
            "Estrategia":  name,
            "Strikes":     "/".join(f"{k:,.0f}" for k in sorted(set(used_K))) if valid else "-",
            "Costo/Prima": neto                      if valid else None,
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
        costs.append({'Fecha': ts, 'Costo': round(neto, 2)})

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

    pl = intrinseco - (neto or 0)
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

@st.fragment(run_every=2)
def vista_libro():
    db = get_db()

    header_col, select_col = st.columns([3, 1])
    with header_col:
        st.markdown("## ACAQuant | Libro")
    with select_col:
        if "selected_ticker" not in st.session_state:
            st.session_state.selected_ticker = TICKERS[0]

        options_short = [short_name(t) for t in TICKERS]
        current_idx   = TICKERS.index(st.session_state.selected_ticker)

        selected_short = st.selectbox(
            "Ticker", options_short,
            index=current_idx,
            label_visibility="collapsed"
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
def vista_opciones():
    db_op     = get_db_opciones()
    meta_col  = get_meta_col()

    docs = list(db_op["OptionsSnapshot"].find({}))
    spot = next((d.get("spot", 0) for d in docs if d.get("spot", 0) > 0), 0) if docs else 0

    # ── cabecera con SPOT, VR y tasa ─────────────────────────────────────
    vr_doc = meta_col.find_one({"type": "vr_ggal"})
    vr_local = vr_doc.get("vr_local", 0) if vr_doc else 0
    vr_adr   = vr_doc.get("vr_adr",   0) if vr_doc else 0

    cfg_doc = meta_col.find_one({"type": "config"})
    tasa_actual = cfg_doc.get("tasa", 0.242) if cfg_doc else 0.242
    if "tasa_display" not in st.session_state:
        st.session_state["tasa_display"] = tasa_actual

    st.markdown("## ACAQuant | Opciones")

    ultimo_ts = max((d.get("updated_at") for d in docs if d.get("updated_at")), default=None) if docs else None
    ts_str = ""
    if ultimo_ts:
        ts_art = ultimo_ts - timedelta(hours=3)
        ts_str = ts_art.strftime("%H:%M:%S")

    col_spot, col_vr, col_tasa, col_ts = st.columns([2, 3, 3, 2])
    with col_spot:
        st.caption("SPOT")
        st.markdown(f"**${spot:,.2f}**" if spot else "N/A")
    with col_vr:
        if vr_local:
            st.metric("VR GGAL (40r)", f"{vr_local:.1%}", delta=f"ADR {vr_adr:.1%}", delta_color="off")
        else:
            st.metric("VR GGAL", "calculando…")
    with col_tasa:
        nueva_tasa = st.number_input(
            "Tasa libre de riesgo",
            min_value=0.0, max_value=3.0,
            value=st.session_state["tasa_display"],
            step=0.005, format="%.3f",
            key="tasa_input",
            help="Cambiá el valor y el motor lo aplicará en ~60s"
        )
        if abs(nueva_tasa - st.session_state["tasa_display"]) > 1e-6:
            meta_col.update_one({"type": "config"}, {"$set": {"tasa": nueva_tasa}}, upsert=True)
            st.session_state["tasa_display"] = nueva_tasa
            st.toast(f"Tasa actualizada a {nueva_tasa:.3f}", icon="✅")
    with col_ts:
        if ts_str:
            st.caption("Última actualización")
            st.markdown(f"**{ts_str}**")

    st.divider()

    if not docs:
        st.warning("Sin datos de opciones. ¿El motor de opciones está corriendo?")
        return

    render_cadena_opciones(docs, spot)

    # ── Volatility Smile ─────────────────────────────────────────────────
    smile_rows = {}
    for d in docs:
        k = d.get("strike")
        t = d.get("tipo")
        iv = d.get("iv")
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


@st.fragment(run_every=60)
def vista_estrategias():
    db_op = get_db_opciones()

    _proj = {"_id": 0, "symbol": 1, "strike": 1, "tipo": 1, "bid": 1, "offer": 1,
             "last": 1, "ev": 1, "delta": 1, "gamma": 1, "theta": 1,
             "iv": 1, "spot": 1, "updated_at": 1}
    docs = list(db_op["OptionsSnapshot"].find({}, _proj))
    spot = next((d.get("spot", 0) for d in docs if d.get("spot", 0) > 0), 0) if docs else 0

    # ── cabecera ─────────────────────────────────────────────────────────
    st.markdown("## ACAQuant | Estrategias Opciones")

    if not docs:
        st.warning("Sin datos de opciones. ¿El motor de opciones está corriendo?")
        return

    # Construir lista de strikes líquidos
    por_strike = {}
    for d in docs:
        k = d.get('strike')
        t = d.get('tipo')
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
        return

    # Strike central por defecto = ATM
    atm_idx_default = min(range(len(liquid_strikes)), key=lambda i: abs(liquid_strikes[i] - spot))
    atm_K_default   = liquid_strikes[atm_idx_default]

    ultimo_ts = max((d.get("updated_at") for d in docs if d.get("updated_at")), default=None)
    ts_str = ""
    if ultimo_ts:
        ts_art = ultimo_ts - timedelta(hours=3)
        ts_str = ts_art.strftime("%H:%M:%S")

    categorias = ["Todas"] + sorted(set(cat for cat, name, legs in STRATEGY_TEMPLATES))

    col_spot, col_strike, col_cat, col_ts = st.columns([2, 3, 3, 2])
    with col_spot:
        st.caption("SPOT")
        st.markdown(f"**${spot:,.2f}**" if spot else "N/A")
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
            options=categorias,
            key="estrategias_cat",
        )
    with col_ts:
        if ts_str:
            st.caption("Última actualización")
            st.markdown(f"**{ts_str}**")

    center_idx = liquid_strikes.index(strike_sel)
    st.divider()

    rows, resolved_legs_list = _calcular_estrategias(
        por_strike, liquid_strikes, center_idx, spot, categoria_sel
    )

    col_tabla, col_graficos = st.columns([2, 3])

    with col_tabla:
        df_est = pd.DataFrame(rows)
        atm_label = " (ATM)" if center_idx == atm_idx_default else ""
        st.caption(
            f"Strike central: {strike_sel:,.0f}{atm_label} | Spot: ${spot:,.2f}  |  "
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
            styler,
            hide_index=True,
            use_container_width=True,
            height=df_height(len(df_est), max_h=700),
            on_select="rerun",
            selection_mode="single-row",
            key="estrategias_tabla",
        )

    with col_graficos:
        sel_rows = selection.selection.rows if hasattr(selection, 'selection') else []
        if not sel_rows:
            st.info("← Seleccioná una estrategia de la tabla para ver los gráficos.")
        else:
            row_idx  = sel_rows[0]
            sel_name = rows[row_idx]["Estrategia"]
            sel_cost = rows[row_idx]["Costo/Prima"]
            sel_legs = resolved_legs_list[row_idx]

            tipo_cost = "DEBIT" if (sel_cost or 0) > 0 else "CREDIT"
            st.markdown(f"**{sel_name}**  |  {tipo_cost} ${abs(sel_cost or 0):.2f}")

            tab_hist, tab_payoff = st.tabs(["Histórico de Costo", "Payoff al Vencimiento"])

            with tab_hist:
                chart_h = _chart_historico_estrategia(db_op, sel_legs, costo_actual=sel_cost)
                if chart_h:
                    st.altair_chart(chart_h, use_container_width=True)
                else:
                    st.info("Sin datos históricos suficientes para esta estrategia.")

            with tab_payoff:
                chart_p, breakevens = _chart_payoff_estrategia(sel_legs, spot, sel_cost)
                if chart_p:
                    st.altair_chart(chart_p, use_container_width=True)
                    be_str = "  |  Break-even: " + "  /  ".join(f"${int(b):,}" for b in breakevens) if breakevens else ""
                    st.caption(f"Línea amarilla = Spot actual (${spot:,.0f}){be_str}")



@st.fragment(run_every=60)
def vista_mercado():
    db = get_db()

    st.markdown("## ACAQuant | Mercado")

    all_snaps = list(db["MarketSnapshot"].find({}))

    if all_snaps:
        ultimo_ts = max(
            (s.get("updated_at") for s in all_snaps if s.get("updated_at")),
            default=None
        )
        last_update_badge(ultimo_ts)
    st.caption("Vista con actualización automática cada 1 minuto.")

    st.divider()

    # Traer último trade enriquecido por ticker (TEA/TEM/Duration/Paridad)
    curvas_tickers = [d["ticker"] for d in db["Curvas"].find({}, {"ticker": 1})]
    pipeline = [
        {"$match": {"ticker": {"$in": curvas_tickers}, "duration": {"$exists": True}}},
        {"$sort": {"timestamp": -1}},
        {"$group": {"_id": "$ticker", "doc": {"$first": "$$ROOT"}}},
    ]
    enriched = {r["_id"]: r["doc"] for r in db["TimeSales"].aggregate(pipeline)}

    all_snaps.sort(
        key=lambda s: s.get("metrics", {}).get("total_money", 0) or 0,
        reverse=True
    )
    render_mercado_table(all_snaps, enriched)


@st.fragment(run_every=30)
def vista_carteras():
    """
    Muestra el contenido de Valuaciones.Carteras:
    posiciones por cuenta con precio de mercado y valuación.
    main_carteras.py (cron en Digital Ocean) actualiza este collection.
    """
    db_val = get_db_valuaciones()

    # ── MEP: leer config manual de Mongo (antes de renderizar) ───────────
    mep_cfg = db_val["Dolar"].find_one({"type": "config"})
    mep_actual = float(mep_cfg.get("mep", 0)) if mep_cfg else 0.0
    if "mep_display" not in st.session_state:
        st.session_state["mep_display"] = mep_actual

    header_col, mep_col = st.columns([3, 1])
    with header_col:
        st.markdown("## ACAQuant | Carteras")
    with mep_col:
        nuevo_mep = st.number_input(
            "MEP", min_value=0.0, max_value=10_000_000.0,
            value=st.session_state["mep_display"],
            step=1.0, format="%.2f",
            key="mep_input", label_visibility="collapsed",
            placeholder="Tipo de cambio MEP",
        )
        if abs(nuevo_mep - st.session_state["mep_display"]) > 1e-4:
            db_val["Dolar"].update_one(
                {"type": "config"},
                {"$set": {"mep": nuevo_mep, "updated_at": datetime.utcnow()}},
                upsert=True
            )
            st.session_state["mep_display"] = nuevo_mep
            st.toast(f"MEP actualizado a ${nuevo_mep:,.2f}", icon="✅")

    docs = list(db_val["Carteras"].find({}, {"_id": 0}))
    if not docs:
        st.warning("Sin datos de carteras. ¿El cron de `main_carteras.py` está corriendo?")
        return

    df = pd.DataFrame(docs)

    # Timestamp de actualización + botón manual
    actualizado = df["actualizado"].dropna().replace("", None).dropna()
    col_ts, col_btn = st.columns([6, 1])
    with col_ts:
        if not actualizado.empty:
            st.caption(f"Última sincronización Aunesa: {actualizado.iloc[0]}")
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

    st.divider()

    # ── filtros ───────────────────────────────────────────────────────────
    cuentas  = sorted(df["id_cuenta"].dropna().unique().tolist())
    carteras = sorted(df["CARTERA"].dropna().unique().tolist()) if "CARTERA" in df.columns else []

    col_fil1, col_fil2, col_resumen = st.columns([2, 2, 5])
    with col_fil1:
        cuenta_sel = st.selectbox("Cuenta", ["Todas"] + cuentas, key="carteras_cuenta")
    with col_fil2:
        cartera_sel = st.selectbox("Cartera", ["Todas"] + carteras, key="carteras_cartera")

    df_view = df.copy()
    if cuenta_sel != "Todas":
        df_view = df_view[df_view["id_cuenta"] == cuenta_sel]
    if cartera_sel != "Todas" and "CARTERA" in df_view.columns:
        df_view = df_view[df_view["CARTERA"] == cartera_sel]

    # ── métricas resumen ──────────────────────────────────────────────────
    total_val = df_view["valuación"].sum()
    mep_val   = st.session_state["mep_display"] or None

    with col_resumen:
        m1, m2 = st.columns(2)
        m1.metric("Valuación ARS", fmt_money(total_val) if total_val else "N/A")
        m2.metric("Valuación USD", fmt_money(total_val / mep_val) if (mep_val and total_val) else "N/A")

    st.divider()

    # ── tabla ─────────────────────────────────────────────────────────────
    col_order = ["TICKER", "EMISOR", "VENCIMIENTO", "CLASE_ACTIVO", "CARTERA",
                 "CALIFICACION", "cantidad", "precio_num", "valuación"]
    if cuenta_sel == "Todas":
        col_order = ["id_cuenta"] + col_order
    cols_present = [c for c in col_order if c in df_view.columns]
    display = df_view[cols_present].copy()
    display.rename(columns={
        "id_cuenta":    "Cuenta",
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
    sort_cols = (["Cuenta"] if cuenta_sel == "Todas" else []) + (["Ticker"] if "Ticker" in display.columns else [])
    if sort_cols:
        display = display.sort_values(sort_cols)

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

    def _fmt_group(grp_df, label_col):
        """Agrega columna USD y formatea ARS. Recibe df con col 'val_raw'."""
        grp_df = grp_df.copy()
        grp_df["Valuación USD"] = grp_df["val_raw"].apply(
            lambda v: fmt_money(v / mep_val) if (mep_val and pd.notna(v)) else "-"
        )
        grp_df["Valuación ARS"] = grp_df["val_raw"].apply(
            lambda v: fmt_money(v) if pd.notna(v) else "-"
        )
        return grp_df[[label_col, "Valuación ARS", "Valuación USD"]]

    # ── resumen inferior ──────────────────────────────────────────────────
    st.divider()
    if cuenta_sel == "Todas":
        st.caption("VALUACIÓN POR CUENTA")
        raw = (
            df_view.groupby("id_cuenta")["valuación"]
            .sum().reset_index()
            .rename(columns={"id_cuenta": "Cuenta", "valuación": "val_raw"})
            .sort_values("val_raw", ascending=False)
        )
        st.dataframe(_fmt_group(raw, "Cuenta"), hide_index=True,
                     use_container_width=True, height=df_height(len(raw)))
        return

    # Cuenta seleccionada + sin cartera específica → tabla por cartera
    if cartera_sel == "Todas" and "CARTERA" in df_view.columns:
        st.caption(f"VALUACIÓN POR CARTERA — Cuenta {cuenta_sel}")
        raw = (
            df_view.groupby("CARTERA")["valuación"]
            .sum().reset_index()
            .rename(columns={"CARTERA": "Cartera", "valuación": "val_raw"})
            .sort_values("val_raw", ascending=False)
        )
        st.dataframe(_fmt_group(raw, "Cartera"), hide_index=True,
                     use_container_width=True, height=df_height(len(raw)))

    # ── gráficos analíticos ───────────────────────────────────────────────
    st.divider()

    # Torta + Tabla Emisor (side by side)
    if "CARTERA" in df_view.columns and "CLASE_ACTIVO" in df_view.columns and "EMISOR" in df_view.columns:
        pie_col = "CLASE_ACTIVO" if cartera_sel != "Todas" else "CARTERA"
        pie_label = "Clase" if cartera_sel != "Todas" else "Cartera"
        pie_data = (
            df_view.groupby(pie_col)["valuación"]
            .sum().reset_index()
            .rename(columns={pie_col: pie_label, "valuación": "Valuación"})
        )
        pie_data = pie_data[pie_data["Valuación"] > 0].copy()
        pie_data = pie_data.sort_values(pie_label)  # orden estable para colores fijos
        total_pie = pie_data["Valuación"].sum()
        pie_data["pct"] = pie_data["Valuación"] / total_pie if total_pie else 0
        # Leyenda con % incluido; dominio fijo = colores estables entre renders
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
        pie_h = max(320, df_height(len(emisor_data), max_h=9999))

        n_cats = len(pie_data)
        legend_cols = min(n_cats, 3)
        arc = alt.Chart(pie_data).mark_arc(innerRadius=60).encode(
            theta=alt.Theta("Valuación:Q"),
            color=alt.Color("leyenda:N",
                            scale=alt.Scale(domain=domain_leyenda, scheme="tableau10"),
                            legend=alt.Legend(
                                title=None,
                                orient="bottom",
                                columns=legend_cols,
                                labelLimit=180,
                                symbolSize=120,
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

    # Gráfico de vencimientos
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
            bar_venc = (bars + bar_labels).properties(
                title="Valuación por Vencimiento", height=420
            )
            st.altair_chart(bar_venc, use_container_width=True, theme="streamlit")

    # ── assets incompletos (colapsado, solo si hay pendientes) ───────────
    if not _incompletos_raw.empty:
        incompletos = _incompletos_raw.rename(columns={
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


def vista_aum():
    import re as _re

    _DIVISOR_100 = {s.lower() for s in {
        "Títulos Públicos",
        "Letras del Tesoro Capitalizables en Pesos",
        "Letras del Tesoro Ajustables por CER en Pesos",
        "Títulos de Deuda",
        "Obligaciones Negociables",
        "Fideicomisos Financieros",
        "Cheques de Pago Diferido",
        "LETES",
    }}
    _FUTUROS = {"Futuros", "Forwards", "Derivados"}

    def _limpiar_unidad(u):
        u = str(u)
        m = _re.search(r'\]\s*\S+\s*-\s*(.+)', u)
        if m:
            return m.group(1).strip()
        m2 = _re.search(r'\]\s*(\S+)', u)
        if m2:
            return m2.group(1).strip()
        return u.strip()

    def _valuar(row):
        precio   = row["precio"]   if pd.notna(row["precio"])   else 1.0
        cantidad = row["cantidad"] if pd.notna(row["cantidad"]) else 0.0
        tipo     = str(row.get("tipoTitulo") or "")
        if any(f.lower() in tipo.lower() for f in _FUTUROS):
            precio += 1.0
        if tipo.lower() in _DIVISOR_100 or tipo.lower().startswith(("letras", "letes")):
            return round((precio * cantidad) / 100, 6)
        return round(precio * cantidad, 6)

    def _render_aum(df_src, moneda, mep, agg_por_tipo=False):
        """Renderiza tabla + pie para un dataframe ya filtrado."""
        df_src = df_src.copy()
        df_src["valuacion"] = df_src.apply(_valuar, axis=1)

        divisor = mep if (moneda == "USD" and mep) else 1.0
        simbolo = "USD " if moneda == "USD" else ""

        total_val = df_src["valuacion"].sum() / divisor
        st.markdown(
            f"<div style='font-size:13px;color:#888;margin-bottom:4px'>Valuación total</div>"
            f"<div style='font-size:28px;font-weight:700;color:#094293'>{simbolo}{fmt_nom(total_val)}</div>",
            unsafe_allow_html=True
        )
        st.markdown("---")

        col_table, col_chart = st.columns([2, 1])

        with col_table:
            if agg_por_tipo:
                display = (
                    df_src.groupby("tipoTitulo", as_index=False)["valuacion"]
                    .sum()
                    .sort_values("valuacion", ascending=False)
                    .reset_index(drop=True)
                )
                display["valuacion"] = display["valuacion"] / divisor
                display["Valuación"] = display["valuacion"].apply(lambda v: f"{simbolo}{fmt_nom(v)}")
                display_show = display[["tipoTitulo", "Valuación"]].copy()
                display_show.columns = ["Tipo", "Valuación"]
                st.dataframe(
                    display_show,
                    hide_index=True,
                    use_container_width=True,
                    height=df_height(len(display_show), max_h=600),
                    column_config={
                        "Tipo":      st.column_config.TextColumn("Tipo",      width="medium"),
                        "Valuación": st.column_config.TextColumn("Valuación", width="small"),
                    }
                )
            else:
                display = (
                    df_src.groupby(["instrumento", "tipoTitulo"], as_index=False)["valuacion"]
                    .sum()
                    .sort_values("valuacion", ascending=False)
                    .reset_index(drop=True)
                )
                display["valuacion"] = display["valuacion"] / divisor
                display["Valuación"] = display["valuacion"].apply(lambda v: f"{simbolo}{fmt_nom(v)}")
                display_show = display[["instrumento", "tipoTitulo", "Valuación"]].copy()
                display_show.columns = ["Instrumento", "Tipo", "Valuación"]
                st.dataframe(
                    display_show,
                    hide_index=True,
                    use_container_width=True,
                    height=df_height(len(display_show), max_h=600),
                    column_config={
                        "Instrumento": st.column_config.TextColumn("Instrumento", width="medium"),
                        "Tipo":        st.column_config.TextColumn("Tipo",        width="small"),
                        "Valuación":   st.column_config.TextColumn("Valuación",   width="small"),
                    }
                )

        with col_chart:
            df_tipo = (
                df_src.groupby("tipoTitulo", as_index=False)["valuacion"]
                .sum()
                .rename(columns={"tipoTitulo": "Tipo", "valuacion": "Valor"})
            )
            df_tipo["Valor"] = df_tipo["Valor"] / divisor
            df_tipo = df_tipo[df_tipo["Valor"] > 0].copy()
            total_v = df_tipo["Valor"].sum()
            df_tipo["pct"] = (df_tipo["Valor"] / total_v * 100).round(1)
            df_tipo["pct_label"] = df_tipo["pct"].apply(lambda x: f"{x:.1f}%")

            base = alt.Chart(df_tipo).encode(
                theta=alt.Theta("Valor:Q", stack=True),
                color=alt.Color(
                    "Tipo:N",
                    scale=alt.Scale(scheme="tableau10"),
                    legend=alt.Legend(orient="bottom", columns=1, labelFontSize=10),
                ),
            )
            arc = base.mark_arc(innerRadius=45, outerRadius=100).encode(
                tooltip=[
                    alt.Tooltip("Tipo:N",  title="Tipo"),
                    alt.Tooltip("Valor:Q", title="Valuación", format=",.0f"),
                    alt.Tooltip("pct:Q",   title="%",         format=".1f"),
                ]
            )
            text = base.mark_text(radius=75, size=13, color="white").encode(
                text=alt.Text("pct_label:N"),
            )
            pie = (arc + text).properties(height=380, padding={"top": 20})
            st.altair_chart(pie, use_container_width=True)

    # ── Carga datos ───────────────────────────────────────────────────────────
    st.markdown("## ACAQuant | AuM")

    df = _cargar_aum()
    if df.empty:
        st.warning("Sin datos. Ejecutá `main_aum.py` para cargar las posiciones.")
        return

    df["instrumento"] = df["unidad"].apply(_limpiar_unidad)
    df = df[~df["instrumento"].str.contains("USDL", na=False)].copy()

    # ── MEP ───────────────────────────────────────────────────────────────────
    db_val  = get_db_valuaciones()
    mep_cfg = db_val["Dolar"].find_one({"type": "config"})
    mep_val = float(mep_cfg.get("mep", 0)) if mep_cfg else 0.0

    # ── Controles ─────────────────────────────────────────────────────────────
    snapshots = sorted(df["fecha_snapshot"].dropna().unique(), reverse=True)
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        snap_sel = st.selectbox("Snapshot", snapshots, key="aum_snap",
                                label_visibility="collapsed",
                                format_func=lambda s: f"Snapshot: {s}")
    with c2:
        modo = st.radio("Vista", ["Total", "Por cuenta"], horizontal=True,
                        key="aum_modo", label_visibility="collapsed")
    with c3:
        moneda = st.radio("Moneda", ["ARS", "USD"], horizontal=True,
                          key="aum_moneda", label_visibility="collapsed")
        if moneda == "USD" and mep_val:
            st.caption(f"MEP: ${mep_val:,.2f}")

    df = df[df["fecha_snapshot"] == snap_sel].copy()

    if modo == "Total":
        agg_sel = st.radio("Agrupar por", ["Tipo", "Instrumento"], horizontal=True,
                           key="aum_agg", label_visibility="collapsed")
        _render_aum(df, moneda, mep_val, agg_por_tipo=(agg_sel == "Tipo"))
    else:
        cuentas    = sorted(df["cuenta"].dropna().unique().tolist())
        cuenta_sel = st.selectbox("Cuenta", cuentas, key="aum_cuenta",
                                  label_visibility="collapsed")
        df_cuenta  = df[df["cuenta"] == cuenta_sel].copy()
        if df_cuenta.empty:
            st.info("Sin posiciones para esta cuenta.")
            return
        _render_aum(df_cuenta, moneda, mep_val)


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

    df = pd.DataFrame(data, index=tickers).T

    def fmt_cell(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        return f"{v:.2%}"

    # Escala rojo → amarillo → verde centrada en la mediana
    todos_vals = [v for row in data.values() for v in row.values() if v is not None and not pd.isna(v)]

    def bg_cell(v):
        if v is None or (isinstance(v, float) and pd.isna(v)) or not todos_vals:
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


def vista_forwards():
    db = get_db()
    st.markdown("## ACAQuant | Forwards")

    curvas_live = set(db["ForwardsLive"].distinct("curva"))
    curvas_hist = set(db["ForwardsHistorico"].distinct("curva"))
    curvas_todas = sorted(curvas_live | curvas_hist)

    if not curvas_todas:
        st.info("Sin datos. ¿El motor de forwards está corriendo?")
        return

    curva_sel = st.selectbox("Curva", curvas_todas)

    tab_live, tab_hist = st.tabs(["Tiempo Real", "Histórico"])

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
            fecha_sel = st.select_slider("Fecha", options=fechas)
            doc_hist = db["ForwardsHistorico"].find_one(
                {"curva": curva_sel, "fecha": fecha_sel}
            )
            if doc_hist:
                render_forward_matrix(doc_hist)


# Ruteo: solo se llama el fragmento activo.
if vista == "Libro":
    vista_libro()
elif vista == "Opciones":
    vista_opciones()
elif vista == "Estrategias Opciones":
    vista_estrategias()
elif vista == "Mercado":
    vista_mercado()
elif vista == "Forwards":
    vista_forwards()
elif vista == "Carteras":
    vista_carteras()
elif vista == "Operaciones":
    vista_operaciones()
elif vista == "AuM":
    vista_aum()
elif vista == "ONs":
    vista_ons()
