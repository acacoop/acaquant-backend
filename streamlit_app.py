import streamlit as st
import pandas as pd
from datetime import datetime
from mongo_manager import get_mongo_client

# ==========================================
# CONFIG
# ==========================================
st.set_page_config(
    layout="wide",
    page_title="ACAQuant | Mesa de Dinero",
    page_icon="📈",
    initial_sidebar_state="expanded"
)

TICKERS = [
    "MERV - XMEV - TZXM6 - 24hs", "MERV - XMEV - S17A6 - 24hs",
    "MERV - XMEV - S30A6 - 24hs", "MERV - XMEV - S29Y6 - 24hs", "MERV - XMEV - T30J6 - 24hs",
    "MERV - XMEV - S15Y6 - 24hs", "MERV - XMEV - TTJ26 - 24hs", "MERV - XMEV - TTS26 - 24hs",
    "MERV - XMEV - TTD26 - 24hs",
    "MERV - XMEV - S31L6 - 24hs", "MERV - XMEV - S31G6 - 24hs", "MERV - XMEV - S30O6 - 24hs",
    "MERV - XMEV - S30N6 - 24hs", "MERV - XMEV - T15E7 - 24hs", "MERV - XMEV - T30A7 - 24hs",
    "MERV - XMEV - T31Y7 - 24hs", "MERV - XMEV - T30J7 - 24hs", "MERV - XMEV - TY30P - 24hs",
    "MERV - XMEV - X15Y6 - 24hs", "MERV - XMEV - X29Y6 - 24hs", "MERV - XMEV - TZX26 - 24hs",
    "MERV - XMEV - X31L6 - 24hs", "MERV - XMEV - TX26 - 24hs",  "MERV - XMEV - TZXO6 - 24hs",
    "MERV - XMEV - X30N6 - 24hs", "MERV - XMEV - TZXD6 - 24hs", "MERV - XMEV - TZXM7 - 24hs",
    "MERV - XMEV - TZXY7 - 24hs", "MERV - XMEV - TZX27 - 24hs", "MERV - XMEV - TX28 - 24hs",
    "MERV - XMEV - TZXD7 - 24hs", "MERV - XMEV - TZX28 - 24hs", "MERV - XMEV - DICP - 24hs",
    "MERV - XMEV - PARP - 24hs"
]

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
        ["Libro", "Mercado", "Opciones"],
        label_visibility="collapsed"
    )


# ==========================================
# FORMAT HELPERS
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

def lag_badge(ts, threshold=5):
    """Muestra línea de última actualización con color según lag."""
    if not ts:
        return
    lag = (datetime.now() - ts).total_seconds()
    color = "#ff4444" if lag > threshold else "#00cc66"
    st.markdown(
        f"<div style='font-size:12px;color:#555;margin-top:-10px'>"
        f"Última actualización: <span style='color:{color}'>"
        f"{ts.strftime('%H:%M:%S')} ({lag:.1f}s atrás)</span></div>",
        unsafe_allow_html=True
    )


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
    st.dataframe(styler, hide_index=True, use_container_width=True)


def render_quant(m):
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
    ]
    st.caption("QUANT ANALYTICS")
    st.dataframe(
        pd.DataFrame(rows, columns=["Métrica", "Valor"]),
        hide_index=True,
        use_container_width=True,
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
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


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
    st.dataframe(styler, hide_index=True, use_container_width=True)


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
    st.dataframe(styler, hide_index=True, use_container_width=True)


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
        vwap       = m.get("vwap",        0) or 0
        if total == 0:
            continue
        intraday = (last_price / open_price - 1) if open_price > 0 and last_price > 0 else None
        rows.append({
            "Ticker":   short_name(ticker),
            "Total $":  fmt_money(total),
            "Buy $":    fmt_money(buy),
            "Sell $":   fmt_money(sell),
            "Open":     open_price  if open_price  > 0 else None,
            "Last":     last_price  if last_price  > 0 else None,
            "VWAP":     vwap        if vwap        > 0 else None,
            "Intraday": intraday,
            "Imbalance": imb,
        })
    if not rows:
        st.info("Todos los tickers sin volumen aún.")
        return
    df = pd.DataFrame(rows)
    styler = (
        df.style
        .map(lambda v: (
            "color: #00cc66; font-weight: bold" if pd.notna(v) and v >= 0 else
            "color: #ff4444; font-weight: bold"
        ) if pd.notna(v) else "", subset=["Intraday"])
        .map(lambda v: (
            "color: #00cc66; font-weight: bold" if v > 0.05 else
            "color: #ff4444; font-weight: bold" if v < -0.05 else
            "color: #aaa"
        ), subset=["Imbalance"])
        .format({
            "Open":      lambda v: f"{v:,.2f}" if pd.notna(v) else "-",
            "Last":      lambda v: f"{v:,.2f}" if pd.notna(v) else "-",
            "VWAP":      lambda v: f"{v:,.2f}" if pd.notna(v) else "-",
            "Intraday":  lambda v: f"{v:+.2%}" if pd.notna(v) else "-",
            "Imbalance": "{:.2%}",
        })
    )
    st.caption("RESUMEN DE MERCADO")
    st.dataframe(styler, hide_index=True, use_container_width=True)


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

    rows = []
    for K in sorted(por_strike.keys()):
        c = por_strike[K].get('CALL', {})
        p = por_strike[K].get('PUT',  {})
        c_mid = (c.get('bid', 0) + c.get('offer', 0)) / 2 if c.get('bid', 0) > 0 and c.get('offer', 0) > 0 else c.get('last', 0)
        p_mid = (p.get('bid', 0) + p.get('offer', 0)) / 2 if p.get('bid', 0) > 0 and p.get('offer', 0) > 0 else p.get('last', 0)
        if c_mid == 0 and p_mid == 0:
            continue
        rows.append({
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
        })

    if not rows:
        st.info("Sin precios disponibles.")
        return

    df = pd.DataFrame(rows)
    styler = (
        df.style
        .map(lambda v: "color: #00cc66; font-weight: bold" if pd.notna(v) else "", subset=["C Bid", "P Bid"])
        .map(lambda v: "color: #ff4444; font-weight: bold" if pd.notna(v) else "", subset=["C Offer", "P Offer"])
        .map(lambda v: "color: #f0c040; font-weight: bold", subset=["STRIKE"])
        .format({
            "C Delta": lambda v: f"{v:.3f}" if pd.notna(v) else "-",
            "C IV %":  lambda v: f"{v:.1f}%" if pd.notna(v) else "-",
            "C Bid":   lambda v: f"{v:.2f}"  if pd.notna(v) else "-",
            "C Offer": lambda v: f"{v:.2f}"  if pd.notna(v) else "-",
            "STRIKE":  "{:,.1f}",
            "P Bid":   lambda v: f"{v:.2f}"  if pd.notna(v) else "-",
            "P Offer": lambda v: f"{v:.2f}"  if pd.notna(v) else "-",
            "P IV %":  lambda v: f"{v:.1f}%" if pd.notna(v) else "-",
            "P Delta": lambda v: f"{v:.3f}"  if pd.notna(v) else "-",
        })
    )
    spot_str = f"${spot:,.2f}" if spot else "N/A"
    st.caption(f"CADENA DE OPCIONES GGAL — SPOT: {spot_str}")
    st.dataframe(styler, hide_index=True, use_container_width=True)


def render_estrategias(docs_map):
    from Opciones.estrategias_opciones import ESTRATEGIAS

    def get_safe(d, key):
        if not d: return 0
        v = d.get(key, 0)
        return v if v is not None else 0

    rows = []
    for estr in ESTRATEGIAS:
        neto, d_net, g_net, t_net, valida = 0, 0, 0, 0, True
        s_c, s_v = 0, 0
        for pata in estr['patas']:
            sym = pata['symbol']
            dat = docs_map.get(sym)
            if not dat:
                valida = False
                break
            qty    = pata['ratio']
            p_off  = get_safe(dat, 'offer')
            p_bid  = get_safe(dat, 'bid')
            p_last = get_safe(dat, 'last')
            px = (p_off if pata['lado'] == 'compra' else p_bid) if (p_off > 0 and p_bid > 0) else p_last
            if px == 0:
                valida = False
                break
            strike_val = get_safe(dat, 'strike')
            if pata['lado'] == 'compra':
                s_c = strike_val
            else:
                s_v = strike_val
            m = 1 if pata['lado'] == 'compra' else -1
            neto  += px  * qty * m
            d_net += get_safe(dat, 'delta') * qty * m
            g_net += get_safe(dat, 'gamma') * qty * m
            t_net += get_safe(dat, 'theta') * qty * m

        f_val = abs(s_c - s_v) - neto if (s_c > 0 and s_v > 0 and neto > 0) else 0
        rows.append({
            "Estrategia": estr['nombre'],
            "Costo":      neto  if valida else None,
            "Finish":     f_val if (valida and f_val > 0) else None,
            "Ratio":      (f_val / neto) if (valida and f_val > 0 and neto > 0) else None,
            "Delta":      d_net if valida else None,
            "Gamma":      g_net if valida else None,
            "Theta":      t_net if valida else None,
        })

    if not rows:
        return

    df = pd.DataFrame(rows)
    styler = (
        df.style
        .map(lambda v: (
            "color: #ff4444; font-weight: bold" if pd.notna(v) and v > 0 else
            "color: #00cc66; font-weight: bold" if pd.notna(v) else
            "color: #555"
        ), subset=["Costo"])
        .format({
            "Costo":  lambda v: f"${v:.2f}" if pd.notna(v) else "Sin Liq",
            "Finish": lambda v: f"${v:.2f}" if pd.notna(v) else "-",
            "Ratio":  lambda v: f"{v:.1%}"  if pd.notna(v) else "-",
            "Delta":  lambda v: f"{v:.3f}"  if pd.notna(v) else "-",
            "Gamma":  lambda v: f"{v:.4f}"  if pd.notna(v) else "-",
            "Theta":  lambda v: f"{v:.2f}"  if pd.notna(v) else "-",
        })
    )
    st.caption("ESTRATEGIAS")
    st.dataframe(styler, hide_index=True, use_container_width=True)


# ==========================================
# VISTAS — cada una es un fragmento independiente que se auto-refresca
# cada 1 segundo. Al cambiar de vista (radio), Streamlit destruye el
# fragmento anterior limpiamente antes de montar el nuevo, eliminando
# el problema de HTML "fantasma" de Opciones que persistía en otras vistas.
# ==========================================

@st.fragment(run_every=1)
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

    lag_badge(snap.get("updated_at"), threshold=5)
    st.divider()

    book          = snap.get("book", {"bids": [], "offers": []})
    metrics       = snap.get("metrics", {})
    hourly_stats  = snap.get("hourly_stats", {})
    recent_trades = snap.get("recent_trades", [])
    top_trades    = snap.get("top_trades", [])

    col_left, col_center, col_right = st.columns([1, 1, 1])
    with col_left:
        render_depth(book)
        st.write("")
        render_quant(metrics)
        st.write("")
        render_hourly(hourly_stats)
    with col_center:
        render_tape(recent_trades)
    with col_right:
        render_whales(top_trades)


@st.fragment(run_every=1)
def vista_opciones():
    db_op = get_db_opciones()

    st.markdown("## 📊 ACAQuant | Opciones GGAL")

    docs = list(db_op["OptionsSnapshot"].find({}))

    if docs:
        ultimo_ts = max((d.get("updated_at") for d in docs if d.get("updated_at")), default=None)
        spot = next((d.get("spot", 0) for d in docs if d.get("spot", 0) > 0), 0)
        lag_badge(ultimo_ts, threshold=10)
    else:
        spot = 0

    st.divider()

    if not docs:
        st.warning("Sin datos de opciones. ¿El motor de opciones está corriendo?")
        return

    docs_map = {d['symbol']: d for d in docs if d.get('symbol')}
    render_cadena_opciones(docs, spot)
    render_estrategias(docs_map)


@st.fragment(run_every=1)
def vista_mercado():
    db = get_db()

    st.markdown("## 🏦 ACAQuant | Mercado")

    all_snaps = list(db["MarketSnapshot"].find({}))

    if all_snaps:
        ultimo_ts = max(
            (s.get("updated_at") for s in all_snaps if s.get("updated_at")),
            default=None
        )
        lag_badge(ultimo_ts, threshold=5)

    st.divider()

    all_snaps.sort(
        key=lambda s: s.get("metrics", {}).get("total_money", 0) or 0,
        reverse=True
    )
    render_mercado_table(all_snaps)


# Ruteo: solo se llama el fragmento activo. Al cambiar de vista,
# el fragmento anterior es destruido limpiamente por Streamlit.
if vista == "Libro":
    vista_libro()
elif vista == "Opciones":
    vista_opciones()
elif vista == "Mercado":
    vista_mercado()
