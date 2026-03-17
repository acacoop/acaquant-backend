import time
import streamlit as st
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
@st.cache_resource
def get_db():
    client = get_mongo_client()
    return client["Trading"]

@st.cache_resource
def get_db_opciones():
    client = get_mongo_client()
    return client["Opciones"]


# ==========================================
# CSS PERSONALIZADO
# ==========================================
st.markdown("""
<style>
    /* Fondo oscuro general */
    .stApp { background-color: #0e1117; }

    /* Headers de sección */
    .section-title {
        font-size: 13px; font-weight: bold; color: #4DA8DA;
        text-align: center; border-bottom: 1px solid #333;
        padding-bottom: 4px; margin-bottom: 8px; letter-spacing: 1px;
    }

    /* Tabla de depth */
    .depth-table { width: 100%; border-collapse: collapse; font-size: 13px; }
    .depth-table td { padding: 2px 6px; }
    .bid-qty  { text-align: right; color: #aaa; }
    .bid-px   { text-align: right; color: #00cc66; font-weight: bold; }
    .ask-px   { text-align: right; color: #ff4444; font-weight: bold; }
    .ask-qty  { text-align: left;  color: #aaa; }

    /* Métricas quant */
    .metric-row { display: flex; justify-content: space-between; font-size: 12px; padding: 2px 0; }
    .metric-label { color: #888; }
    .metric-value { color: #e5e5e5; font-weight: bold; }

    /* Tape */
    .tape-buy  { color: #00cc66; font-weight: bold; }
    .tape-sell { color: #ff4444; font-weight: bold; }
    .tape-mid  { color: #aaaaaa; }

    /* Separador de secciones */
    .section-sep { border: none; border-top: 1px solid #333; margin: 8px 0; }

    /* Ocultar toolbar de dataframe */
    [data-testid="stElementToolbar"] { display: none; }

    /* Tabla de opciones */
    .opt-table { width: 100%; border-collapse: collapse; font-size: 12px; }
    .opt-table th { color: #555; font-size: 11px; padding: 3px 6px; border-bottom: 1px solid #222; }
    .opt-table td { padding: 2px 6px; }
    .opt-strike { text-align: center; color: #f0c040; font-weight: bold; background: #1a1a2e; }
    .opt-itm    { background: #0d1f0d; }
    .opt-otm    { background: #0e1117; }
    .c-bid      { text-align: right; color: #00cc66; font-weight: bold; }
    .c-offer    { text-align: right; color: #ff4444; font-weight: bold; }
    .c-iv       { text-align: right; color: #aaa; }
    .c-delta    { text-align: right; color: #7eb8f7; }
    .c-last     { text-align: right; color: #ccc; }
</style>
""", unsafe_allow_html=True)


# ==========================================
# SIDEBAR - NAVEGACIÓN
# ==========================================
with st.sidebar:
    st.markdown("### 📈 ACAQuant")
    st.markdown("---")
    vista = st.radio(
        "Vista",
        ["Libro", "Opciones"],
        label_visibility="collapsed"
    )


# ==========================================
# HELPERS DE RENDER - LIBRO
# ==========================================
def fmt_money(v):
    if v >= 1_000_000_000: return f"${v/1_000_000_000:.1f}B"
    if v >= 1_000_000:     return f"${v/1_000_000:.1f}M"
    if v >= 1_000:         return f"${v/1_000:.0f}K"
    return f"${v:.0f}"

def fmt_nom(v):
    if v >= 1_000_000_000: return f"{v/1_000_000_000:.2f}B"
    if v >= 1_000_000:     return f"{v/1_000_000:.1f}M"
    if v >= 1_000:         return f"{v/1_000:.0f}K"
    return f"{v:.0f}"

def render_depth(book):
    bids   = book.get("bids", [])
    offers = book.get("offers", [])
    rows = []
    for i in range(5):
        bq = f"{bids[i]['size']:,.0f}"   if i < len(bids)   else "-"
        bp = f"{bids[i]['price']:,.2f}"  if i < len(bids)   else "-"
        ap = f"{offers[i]['price']:,.2f}" if i < len(offers) else "-"
        aq = f"{offers[i]['size']:,.0f}"  if i < len(offers) else "-"
        rows.append(f"""
            <tr>
                <td class='bid-qty'>{bq}</td>
                <td class='bid-px'>{bp}</td>
                <td class='ask-px'>{ap}</td>
                <td class='ask-qty'>{aq}</td>
            </tr>""")
    return f"""
        <div class='section-title'>DEPTH</div>
        <table class='depth-table'>
            <thead><tr>
                <th style='text-align:right;color:#555;font-size:11px'>Bid Q</th>
                <th style='text-align:right;color:#555;font-size:11px'>Bid P</th>
                <th style='text-align:right;color:#555;font-size:11px'>Ask P</th>
                <th style='text-align:left; color:#555;font-size:11px'>Ask Q</th>
            </tr></thead>
            <tbody>{''.join(rows)}</tbody>
        </table>"""

def render_quant(m):
    prog_filled = int(m.get('progreso', 0) * 10)
    prog_bar = "█" * prog_filled + "░" * (10 - prog_filled)
    vpin_prom = m.get('vpin_prom', 0)
    vpin_color = "#ff4444" if vpin_prom > 0.7 else "#e5e5e5"

    rows = [
        ("Micro-Price",        f"{m.get('micro_price', 0):,.4f}"),
        ("Spread",             f"{m.get('spread', 0):,.2f}"),
        ("Order Imbalance",    f"{m.get('imbalance', 0):.2%}"),
        ("---", "---"),
        ("VPIN Promedio",      f"<span style='color:{vpin_color}'>{vpin_prom:.2%}</span>"),
        ("VPIN Vivo",          f"{m.get('vpin_vivo', 0):.2%}"),
        ("Bucket Progress",    f"{prog_bar} {m.get('progreso', 0):.1%}"),
        ("Bucket B / S",       f"<span style='color:#00cc66'>{fmt_nom(m.get('buy_b',0))}</span> / <span style='color:#ff4444'>{fmt_nom(m.get('sell_b',0))}</span>"),
        ("---", "---"),
        ("Nominales Totales",  fmt_nom(m.get('total_nominals', 0))),
        ("VWAP (Daily)",       f"${m.get('vwap', 0):,.2f}"),
        ("Total Money",        fmt_money(m.get('total_money', 0))),
        ("Buy / Sell Session", f"<span style='color:#00cc66'>{fmt_money(m.get('buy_money',0))}</span> / <span style='color:#ff4444'>{fmt_money(m.get('sell_money',0))}</span>"),
    ]

    html_rows = []
    for label, value in rows:
        if label == "---":
            html_rows.append("<hr class='section-sep'>")
        else:
            html_rows.append(f"<div class='metric-row'><span class='metric-label'>{label}</span><span class='metric-value'>{value}</span></div>")

    return f"<div class='section-title'>QUANT ANALYTICS</div>{''.join(html_rows)}"

def render_hourly(hourly_stats):
    rows = []
    for h in range(10, 18):
        d = hourly_stats.get(str(h), {"buy": 0, "sell": 0, "total": 0})
        total = d.get("total", 0)
        buy   = d.get("buy", 0)
        sell  = d.get("sell", 0)
        if total > 0:
            buy_blocks  = int((buy  / total) * 10)
            sell_blocks = int((sell / total) * 10)
            bar = (f"<span style='color:#00cc66'>{'█' * buy_blocks}</span>"
                   f"<span style='color:#ff4444'>{'█' * sell_blocks}</span>")
        else:
            bar = "<span style='color:#333'>──────────</span>"
        rows.append(
            f"<div class='metric-row'>"
            f"<span class='metric-label'>{h}hs</span>"
            f"<span style='font-size:11px;color:#888'>{fmt_money(total) if total > 0 else '$0'}</span>"
            f"<span>{bar}</span>"
            f"</div>"
        )
    return f"<div class='section-title'>HOURLY VOL</div>{''.join(rows)}"

def render_tape(trades):
    rows = []
    for t in trades[:30]:
        side = t.get("side", "MID")
        css  = "tape-buy" if side == "BUY" else ("tape-sell" if side == "SELL" else "tape-mid")
        ts   = t.get("timestamp")
        hora = ts.strftime("%H:%M:%S") if hasattr(ts, 'strftime') else str(ts)[:8]
        rows.append(
            f"<tr>"
            f"<td style='color:#555;font-size:12px'>{hora}</td>"
            f"<td style='text-align:right;font-size:12px;color:#ccc'>{t.get('price', 0):,.2f}</td>"
            f"<td style='text-align:right;font-size:12px;color:#aaa'>{t.get('size', 0):,.0f}</td>"
            f"<td style='text-align:center' class='{css}'>{side}</td>"
            f"</tr>"
        )
    return f"""
        <div class='section-title'>TAPE</div>
        <table style='width:100%;border-collapse:collapse;font-size:12px'>
            <thead><tr>
                <th style='color:#555;font-size:11px;text-align:left'>Hora</th>
                <th style='color:#555;font-size:11px;text-align:right'>Px</th>
                <th style='color:#555;font-size:11px;text-align:right'>Sz</th>
                <th style='color:#555;font-size:11px;text-align:center'>Side</th>
            </tr></thead>
            <tbody>{''.join(rows)}</tbody>
        </table>"""

def render_whales(top_trades):
    rows = []
    for t in top_trades[:15]:
        side  = t.get("side", "MID")
        css   = "tape-buy" if side == "BUY" else ("tape-sell" if side == "SELL" else "tape-mid")
        money = t.get("money", (t.get("price", 0) / 100) * t.get("size", 0))
        rows.append(
            f"<tr>"
            f"<td style='text-align:right;font-size:12px;color:#ccc'>{t.get('price', 0):,.2f}</td>"
            f"<td style='text-align:right;font-size:12px;color:#f0c040;font-weight:bold'>{fmt_money(money)}</td>"
            f"<td style='text-align:center' class='{css}'>{side}</td>"
            f"</tr>"
        )
    return f"""
        <div class='section-title'>TOP 15 WHALES (CASH)</div>
        <table style='width:100%;border-collapse:collapse;font-size:12px'>
            <thead><tr>
                <th style='color:#555;font-size:11px;text-align:right'>Px</th>
                <th style='color:#555;font-size:11px;text-align:right'>Monto ($)</th>
                <th style='color:#555;font-size:11px;text-align:center'>Side</th>
            </tr></thead>
            <tbody>{''.join(rows)}</tbody>
        </table>"""


# ==========================================
# HELPERS DE RENDER - OPCIONES
# ==========================================
def render_cadena_opciones(docs, spot):
    """Construye la tabla cadena CALL | STRIKE | PUT desde los docs de MongoDB."""
    # Agrupar por strike
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
        return "<p style='color:#888'>Sin datos de opciones. ¿El motor de opciones está corriendo?</p>"

    def cell(v, fmt=".2f"):
        if v is None or v == 0:
            return "-"
        return f"{v:{fmt}}"

    def iv_str(v):
        if v is None or v == 0:
            return "-"
        return f"{v*100:.1f}%"

    rows = []
    for K in sorted(por_strike.keys()):
        c = por_strike[K].get('CALL', {})
        p = por_strike[K].get('PUT', {})

        itm_call = spot > 0 and K < spot
        itm_put  = spot > 0 and K > spot
        css_c = "opt-itm" if itm_call else "opt-otm"
        css_p = "opt-itm" if itm_put  else "opt-otm"

        rows.append(f"""
        <tr>
            <td class='{css_c} c-bid'>{cell(c.get('bid'))}</td>
            <td class='{css_c} c-offer'>{cell(c.get('offer'))}</td>
            <td class='{css_c} c-last'>{cell(c.get('last'))}</td>
            <td class='{css_c} c-iv'>{iv_str(c.get('iv'))}</td>
            <td class='{css_c} c-delta'>{cell(c.get('delta'), '.3f')}</td>
            <td class='opt-strike'>{K:,.1f}</td>
            <td class='{css_p} c-delta'>{cell(p.get('delta'), '.3f')}</td>
            <td class='{css_p} c-iv'>{iv_str(p.get('iv'))}</td>
            <td class='{css_p} c-last'>{cell(p.get('last'))}</td>
            <td class='{css_p} c-offer'>{cell(p.get('offer'))}</td>
            <td class='{css_p} c-bid'>{cell(p.get('bid'))}</td>
        </tr>""")

    return f"""
    <div class='section-title'>CADENA DE OPCIONES GGAL — SPOT: {'${:,.2f}'.format(spot) if spot else 'N/A'}</div>
    <table class='opt-table'>
        <thead>
            <tr>
                <th colspan='5' style='text-align:center;color:#00cc66'>CALL</th>
                <th style='text-align:center;color:#f0c040'>STRIKE</th>
                <th colspan='5' style='text-align:center;color:#ff4444'>PUT</th>
            </tr>
            <tr>
                <th style='text-align:right'>Bid</th>
                <th style='text-align:right'>Offer</th>
                <th style='text-align:right'>Last</th>
                <th style='text-align:right'>IV</th>
                <th style='text-align:right'>Delta</th>
                <th></th>
                <th style='text-align:right'>Delta</th>
                <th style='text-align:right'>IV</th>
                <th style='text-align:right'>Last</th>
                <th style='text-align:right'>Offer</th>
                <th style='text-align:right'>Bid</th>
            </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
    </table>"""


# ==========================================
# VISTA: LIBRO
# ==========================================
if vista == "Libro":
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

    snap = db["MarketSnapshot"].find_one({"ticker": ticker})

    if not snap:
        st.warning(f"Sin datos para {ticker}. ¿El motor está corriendo?")
        time.sleep(2)
        st.rerun()

    updated_at = snap.get("updated_at")
    if updated_at:
        lag = (datetime.now() - updated_at).total_seconds()
        lag_color = "#ff4444" if lag > 5 else "#00cc66"
        st.markdown(
            f"<div style='font-size:12px;color:#555;margin-top:-10px'>"
            f"Última actualización: <span style='color:{lag_color}'>"
            f"{updated_at.strftime('%H:%M:%S')} ({lag:.1f}s atrás)</span></div>",
            unsafe_allow_html=True
        )

    st.divider()

    col_left, col_center, col_right = st.columns([1, 1, 1])

    book         = snap.get("book", {"bids": [], "offers": []})
    metrics      = snap.get("metrics", {})
    hourly_stats = snap.get("hourly_stats", {})
    recent_trades= snap.get("recent_trades", [])
    top_trades   = snap.get("top_trades", [])

    with col_left:
        st.markdown(render_depth(book), unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(render_quant(metrics), unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(render_hourly(hourly_stats), unsafe_allow_html=True)

    with col_center:
        st.markdown(render_tape(recent_trades), unsafe_allow_html=True)

    with col_right:
        st.markdown(render_whales(top_trades), unsafe_allow_html=True)

    time.sleep(1)
    st.rerun()


# ==========================================
# VISTA: OPCIONES
# ==========================================
elif vista == "Opciones":
    db_op = get_db_opciones()

    st.markdown("## 📊 ACAQuant | Opciones GGAL")

    # Leer el último registro por símbolo
    pipeline = [
        {"$sort": {"timestamp": -1}},
        {"$group": {"_id": "$symbol", "doc": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$doc"}}
    ]
    docs = list(db_op["Data"].aggregate(pipeline))

    # Timestamp de última actualización
    if docs:
        ultimo_ts = max((d.get("timestamp") for d in docs if d.get("timestamp")), default=None)
        spot = next((d.get("spot", 0) for d in docs if d.get("spot", 0) > 0), 0)
        if ultimo_ts:
            lag = (datetime.now() - ultimo_ts).total_seconds()
            lag_color = "#ff4444" if lag > 10 else "#00cc66"
            st.markdown(
                f"<div style='font-size:12px;color:#555;margin-top:-10px'>"
                f"Última actualización: <span style='color:{lag_color}'>"
                f"{ultimo_ts.strftime('%H:%M:%S')} ({lag:.1f}s atrás)</span></div>",
                unsafe_allow_html=True
            )
    else:
        spot = 0

    st.divider()

    if not docs:
        st.warning("Sin datos de opciones. ¿El motor de opciones está corriendo?")
    else:
        st.markdown(render_cadena_opciones(docs, spot), unsafe_allow_html=True)

    time.sleep(2)
    st.rerun()
