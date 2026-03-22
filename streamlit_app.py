import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime, timedelta
from mongo_manager import get_mongo_client
from tickers import MERV_TICKERS as TICKERS

# ==========================================
# CONFIG
# ==========================================
st.set_page_config(
    layout="wide",
    page_title="ACAQuant | Mesa de Dinero",
    page_icon="📈",
    initial_sidebar_state="expanded"
)

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
    st.markdown("### 📈 ACAQuant")
    st.markdown("---")
    vista = st.radio(
        "Vista",
        ["Libro", "Mercado", "Opciones", "Estrategias", "Carteras"],
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
    closing = m.get('closing_price', 0) or 0
    last    = m.get('last_price', 0) or 0
    vs_cierre = (last / closing - 1) if closing > 0 and last > 0 else None

    rows = [
        ("Micro-Price",       f"{m.get('micro_price', 0):,.4f}"),
        ("Spread",            f"{m.get('spread', 0):,.2f}"),
        ("Order Imbalance",   f"{m.get('imbalance', 0):.2%}"),
        ("VPIN Promedio",     f"{m.get('vpin_prom', 0):.2%}"),
        ("VPIN Vivo",         f"{m.get('vpin_vivo', 0):.2%}"),
        ("Bucket Progress",   f"{m.get('progreso', 0):.1%}"),
        ("Bucket Buy",        fmt_nom(m.get('buy_b', 0))),
        ("Bucket Sell",       fmt_nom(m.get('sell_b', 0))),
        ("Nominales Totales", fmt_nom(m.get('total_nominals', 0))),
        ("VWAP (Daily)",      f"${m.get('vwap', 0):,.2f}"),
        ("Total Money",       fmt_money(m.get('total_money', 0))),
        ("Buy Session",       fmt_money(m.get('buy_money', 0))),
        ("Sell Session",      fmt_money(m.get('sell_money', 0))),
        ("Cierre Anterior",   f"${closing:,.2f}" if closing > 0 else "-"),
        ("Vs. Cierre",        f"{vs_cierre:+.2%}" if vs_cierre is not None else "-"),
    ]
    st.caption("QUANT ANALYTICS")
    st.dataframe(
        pd.DataFrame(rows, columns=["Métrica", "Valor"]),
        hide_index=True,
        use_container_width=True,
        height=df_height(len(rows)),
    )


def render_hourly(hourly_stats):
    rows = []
    for h in range(10, 18):
        d = hourly_stats.get(str(h), {"buy": 0, "sell": 0, "total": 0})
        rows.append({
            "Hora":  f"{h}hs",
            "Total": fmt_money(d.get("total", 0)),
            "Buy":   fmt_money(d.get("buy", 0)),
            "Sell":  fmt_money(d.get("sell", 0)),
        })
    st.caption("HOURLY VOL")
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, height=df_height(8))


def render_tape(trades):
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
    st.dataframe(styler, hide_index=True, use_container_width=True, height=df_height(len(rows), max_h=1200))


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
def render_mercado_table(snaps):
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
        rows.append({
            "Ticker":    short_name(ticker),
            "Total $":   fmt_money(total),
            "Buy $":     fmt_money(buy),
            "Sell $":    fmt_money(sell),
            "Open":      open_price   if open_price  > 0 else None,
            "Last":      last_price   if last_price  > 0 else None,
            "Cierre":    closing      if closing      > 0 else None,
            "VWAP":      vwap         if vwap         > 0 else None,
            "Intraday":  intraday,
            "Vs Cierre": vs_cierre,
            "Imbalance": imb,
        })
    if not rows:
        st.info("Todos los tickers sin volumen aún.")
        return
    df = pd.DataFrame(rows)

    def pct_color(v):
        if pd.isna(v): return ""
        return "color: #00cc66; font-weight: bold" if v >= 0 else "color: #ff4444; font-weight: bold"

    styler = (
        df.style
        .map(pct_color, subset=["Intraday", "Vs Cierre"])
        .map(lambda v: (
            "color: #00cc66; font-weight: bold" if v > 0.05 else
            "color: #ff4444; font-weight: bold" if v < -0.05 else
            "color: #aaa"
        ), subset=["Imbalance"])
        .format({
            "Open":      lambda v: f"{v:,.2f}" if pd.notna(v) else "-",
            "Last":      lambda v: f"{v:,.2f}" if pd.notna(v) else "-",
            "Cierre":    lambda v: f"{v:,.2f}" if pd.notna(v) else "-",
            "VWAP":      lambda v: f"{v:,.2f}" if pd.notna(v) else "-",
            "Intraday":  lambda v: f"{v:+.2%}" if pd.notna(v) else "-",
            "Vs Cierre": lambda v: f"{v:+.2%}" if pd.notna(v) else "-",
            "Imbalance": "{:.2%}",
        })
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
    # Greeks heatmap: background_gradient sobre Delta e IV
    heatmap_cols = [c for c in ["C IV %", "C Delta", "P IV %", "P Delta"] if c in df.columns]
    if heatmap_cols:
        styler = styler.background_gradient(subset=heatmap_cols, cmap="RdYlGn", axis=0)

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
    t = []
    # Bull Call Spreads escalonados: compra centro, vende +n
    for n in range(1, 5):
        t.append((f"Bull Call Spread +{n}", [(0,'CALL','buy',1), (+n,'CALL','sell',1)]))
    # Bear Put Spreads escalonados: compra centro, vende -n
    for n in range(1, 5):
        t.append((f"Bear Put Spread  -{n}", [(0,'PUT','buy',1), (-n,'PUT','sell',1)]))
    # Straddle
    t.append(("Straddle ATM", [(0,'CALL','buy',1), (0,'PUT','buy',1)]))
    # Strangles simétricos
    for n in range(1, 4):
        t.append((f"Strangle         {n}w", [(+n,'CALL','buy',1), (-n,'PUT','buy',1)]))
    # Ratio Call 1×2
    for n in range(1, 4):
        t.append((f"Ratio Call 1×2   +{n}", [(0,'CALL','buy',1), (+n,'CALL','sell',2)]))
    # Ratio Put 1×2
    for n in range(1, 4):
        t.append((f"Ratio Put  1×2   -{n}", [(0,'PUT','buy',1), (-n,'PUT','sell',2)]))
    # Call Backspread (crédito / neutral-alcista)
    for n in range(1, 3):
        t.append((f"Call Backspread  +{n}", [(0,'CALL','sell',1), (+n,'CALL','buy',2)]))
    # Put Backspread (crédito / neutral-bajista)
    for n in range(1, 3):
        t.append((f"Put Backspread   -{n}", [(0,'PUT','sell',1), (-n,'PUT','buy',2)]))
    # Iron Condors con distintas alas
    t.append(("Iron Condor  1|2", [(-2,'PUT','buy',1),(-1,'PUT','sell',1),(+1,'CALL','sell',1),(+2,'CALL','buy',1)]))
    t.append(("Iron Condor  2|3", [(-3,'PUT','buy',1),(-2,'PUT','sell',1),(+2,'CALL','sell',1),(+3,'CALL','buy',1)]))
    t.append(("Iron Condor  1|3", [(-3,'PUT','buy',1),(-1,'PUT','sell',1),(+1,'CALL','sell',1),(+3,'CALL','buy',1)]))
    # Short Straddle / Strangle (venta de vol)
    t.append(("Short Straddle",   [(0,'CALL','sell',1), (0,'PUT','sell',1)]))
    for n in range(1, 3):
        t.append((f"Short Strangle   {n}w", [(+n,'CALL','sell',1), (-n,'PUT','sell',1)]))
    return t

STRATEGY_TEMPLATES = _build_strategy_templates()


def render_estrategias_dinamicas(docs, spot, por_strike=None, liquid_strikes=None, center_idx=None):
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
    for name, legs in STRATEGY_TEMPLATES:
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
# VISTAS (st.fragment → auto-refresh 1s, sin sleep ni rerun global)
# ==========================================

@st.fragment(run_every=2)
def vista_libro():
    db = get_db()

    header_col, select_col = st.columns([3, 1])
    with header_col:
        st.markdown("## 📈 ACAQuant | Mesa de Dinero")
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

    # Fila 1: depth+quant | tape | whales
    col_left, col_center, col_right = st.columns([1, 1, 1])
    with col_left:
        render_depth(book)
        st.write("")
        render_quant(metrics)
    with col_center:
        render_tape(recent_trades)
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
                .properties(height=220)
            )
            st.caption("LAST MINUTES")
            st.altair_chart(chart, use_container_width=True)


@st.fragment(run_every=2)
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

    col_titulo, col_spot, col_vr, col_tasa = st.columns([3, 2, 3, 3])
    with col_titulo:
        st.markdown("## 📊 Opciones GGAL")
    with col_spot:
        st.metric("SPOT", f"${spot:,.2f}" if spot else "N/A")
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

    if docs:
        ultimo_ts = max((d.get("updated_at") for d in docs if d.get("updated_at")), default=None)
        last_update_badge(ultimo_ts)

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


@st.fragment(run_every=2)
def vista_estrategias():
    db_op = get_db_opciones()

    docs = list(db_op["OptionsSnapshot"].find({}))
    spot = next((d.get("spot", 0) for d in docs if d.get("spot", 0) > 0), 0) if docs else 0

    # ── cabecera ─────────────────────────────────────────────────────────
    col_titulo, col_spot, col_strike = st.columns([3, 2, 4])
    with col_titulo:
        st.markdown("## 🎯 Estrategias GGAL")
    with col_spot:
        st.metric("SPOT", f"${spot:,.2f}" if spot else "N/A")

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

    with col_strike:
        strike_sel = st.selectbox(
            "Strike central",
            options=liquid_strikes,
            index=atm_idx_default,
            format_func=lambda k: f"{k:,.0f}{'  ← ATM' if k == atm_K_default else ''}",
            key="estrategias_strike",
        )

    center_idx = liquid_strikes.index(strike_sel)

    if docs:
        ultimo_ts = max((d.get("updated_at") for d in docs if d.get("updated_at")), default=None)
        last_update_badge(ultimo_ts)

    st.divider()

    render_estrategias_dinamicas(docs, spot, por_strike=por_strike,
                                  liquid_strikes=liquid_strikes, center_idx=center_idx)

    # ── Greeks agregados del portfolio ATM ───────────────────────────────
    atm_K = liquid_strikes[center_idx]
    atm_data = por_strike.get(atm_K, {})
    if atm_data:
        st.divider()
        st.caption(f"GREEKS NETOS — Strike {atm_K:,.0f} (CALL + PUT)")
        agg_cols = st.columns(4)
        for col, key, label in zip(
            agg_cols,
            ["delta", "gamma", "theta", "iv"],
            ["Delta neto", "Gamma neto", "Theta neto", "IV media"]
        ):
            vals = [d.get(key, 0) or 0 for d in atm_data.values() if d]
            if key == "iv":
                v = sum(vals) / len(vals) if vals else 0
                col.metric(label, f"{v:.2%}" if v else "-")
            else:
                v = sum(vals)
                col.metric(label, f"{v:.4f}" if v else "-")


@st.fragment(run_every=2)
def vista_mercado():
    db = get_db()

    st.markdown("## 🏦 ACAQuant | Mercado")

    all_snaps = list(db["MarketSnapshot"].find({}))

    if all_snaps:
        ultimo_ts = max(
            (s.get("updated_at") for s in all_snaps if s.get("updated_at")),
            default=None
        )
        last_update_badge(ultimo_ts)

    st.divider()

    all_snaps.sort(
        key=lambda s: s.get("metrics", {}).get("total_money", 0) or 0,
        reverse=True
    )
    render_mercado_table(all_snaps)


@st.fragment(run_every=30)
def vista_carteras():
    """
    Muestra el contenido de Valuaciones.Carteras:
    posiciones por cuenta con precio de mercado y valuación.
    main_carteras.py (cron en Digital Ocean) actualiza este collection.
    """
    db_val = get_db_valuaciones()

    st.markdown("## 💼 ACAQuant | Carteras")

    docs = list(db_val["Carteras"].find({}, {"_id": 0}))
    if not docs:
        st.warning("Sin datos de carteras. ¿El cron de `main_carteras.py` está corriendo?")
        return

    df = pd.DataFrame(docs)

    # Timestamp de actualización
    actualizado = df["actualizado"].dropna().replace("", None).dropna()
    if not actualizado.empty:
        st.caption(f"Última sincronización Aunesa: {actualizado.iloc[0]}")

    # Convertir precio a numérico (puede venir como string vacío para no-FCI)
    df["precio_num"] = pd.to_numeric(df["precio"], errors="coerce")
    df["cantidad"]   = pd.to_numeric(df["cantidad"], errors="coerce").fillna(0)
    df["valuación"]  = df["cantidad"] * df["precio_num"]

    st.divider()

    # ── selector de cuenta ────────────────────────────────────────────────
    cuentas = sorted(df["id_cuenta"].dropna().unique().tolist())
    col_fil, col_resumen = st.columns([2, 5])
    with col_fil:
        cuenta_sel = st.selectbox(
            "Cuenta", ["Todas"] + cuentas,
            key="carteras_cuenta"
        )

    df_view = df if cuenta_sel == "Todas" else df[df["id_cuenta"] == cuenta_sel]

    # ── métricas resumen ──────────────────────────────────────────────────
    total_val = df_view["valuación"].sum()
    n_pos     = len(df_view)
    with col_resumen:
        m1, m2, m3 = st.columns(3)
        m1.metric("Posiciones", n_pos)
        m2.metric("Valuación total", fmt_money(total_val) if total_val else "N/A")
        m3.metric("Cuenta(s)", cuenta_sel)

    st.divider()

    # ── tabla ─────────────────────────────────────────────────────────────
    display = df_view[["id_cuenta", "unidad", "cantidad", "precio_num", "valuación"]].copy()
    display.columns = ["Cuenta", "Instrumento", "Cantidad", "Precio", "Valuación"]
    display = display.sort_values(["Cuenta", "Instrumento"])

    styler = (
        display.style
        .map(lambda v: (
            "color: #00cc66; font-weight: bold" if pd.notna(v) and v > 0 else
            "color: #ff4444; font-weight: bold" if pd.notna(v) and v < 0 else ""
        ), subset=["Cantidad", "Valuación"])
        .format({
            "Cantidad":  lambda v: f"{v:,.0f}"  if pd.notna(v) else "-",
            "Precio":    lambda v: f"{v:,.4f}"  if pd.notna(v) else "-",
            "Valuación": lambda v: fmt_money(v) if pd.notna(v) else "-",
        })
    )
    st.dataframe(styler, hide_index=True, use_container_width=True,
                 height=df_height(len(display), max_h=900))

    # ── resumen por cuenta ────────────────────────────────────────────────
    if cuenta_sel == "Todas":
        st.divider()
        st.caption("VALUACIÓN POR CUENTA")
        by_account = (
            df.groupby("id_cuenta")["valuación"]
            .sum()
            .reset_index()
            .rename(columns={"id_cuenta": "Cuenta", "valuación": "Valuación"})
            .sort_values("Valuación", ascending=False)
        )
        by_account["Valuación"] = by_account["Valuación"].apply(
            lambda v: fmt_money(v) if pd.notna(v) else "-"
        )
        st.dataframe(by_account, hide_index=True, use_container_width=True,
                     height=df_height(len(by_account)))


# Ruteo: solo se llama el fragmento activo.
if vista == "Libro":
    vista_libro()
elif vista == "Opciones":
    vista_opciones()
elif vista == "Estrategias":
    vista_estrategias()
elif vista == "Mercado":
    vista_mercado()
elif vista == "Carteras":
    vista_carteras()
