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
        ["Libro", "Mercado", "Opciones", "Estrategias Opciones", "Carteras"],
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

    for col in ["C IV %", "P IV %"]:
        if col in df.columns:
            styler = styler.applymap(_iv_color, subset=[col])
    for col in ["C Delta", "P Delta"]:
        if col in df.columns:
            styler = styler.applymap(_delta_color, subset=[col])

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
        st.markdown("## ACAQuant | Opciones")
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
        st.markdown("## ACAQuant | Estrategias Opciones")
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

    st.markdown("## ACAQuant | Mercado")

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

    # Timestamp de actualización
    actualizado = df["actualizado"].dropna().replace("", None).dropna()
    if not actualizado.empty:
        st.caption(f"Última sincronización Aunesa: {actualizado.iloc[0]}")

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


# Ruteo: solo se llama el fragmento activo.
if vista == "Libro":
    vista_libro()
elif vista == "Opciones":
    vista_opciones()
elif vista == "Estrategias Opciones":
    vista_estrategias()
elif vista == "Mercado":
    vista_mercado()
elif vista == "Carteras":
    vista_carteras()
