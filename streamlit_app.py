import time
import streamlit as st
import pandas as pd
import pyRofex
from datetime import datetime
from mongo_manager import get_mongo_client
from session_manager import inicializar_sesion
from websocket_manager import WebSocketManager
from main_arbitrage import ArbitrageEngine
from main_on import MarketManager, TICKERS_MEP
from live_pricing_bonds.db_bonds import cargar_catalogo_bonos

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
@st.cache_resource
def get_db():
    client = get_mongo_client()
    return client["Trading"]

@st.cache_resource
def get_db_opciones():
    client = get_mongo_client()
    return client["Opciones"]

@st.cache_resource
def get_db_valuaciones():
    client = get_mongo_client()
    return client["Valuaciones"]


# ==========================================
# ENGINE HUB (singleton por proceso Streamlit)
# Hostea ArbitrageEngine + MarketManager
# con UNA sola conexión WebSocket a Rofex.
# ==========================================
class _EngineHub:
    def __init__(self):
        self.arb        = ArbitrageEngine()
        self.on_engine  = None
        self.status     = "initializing"
        self.error      = None
        self._last_tick = 0.0

    def update_price(self, ticker, data):
        """El WebSocketManager llama aquí; ruteamos a ambos sub-engines."""
        self._last_tick = time.time()
        self.arb.update_price(ticker, data)
        if self.on_engine:
            self.on_engine.update_price(ticker, data)

    def is_alive(self):
        return (time.time() - self._last_tick) < 30


@st.cache_resource
def get_engine_hub():
    """
    Crea el hub UNA SOLA VEZ por proceso Streamlit.
    Inicializa sesión, cataloga ambos motores y abre el WebSocket.
    """
    hub = _EngineHub()
    try:
        if not inicializar_sesion():
            hub.status = "error"
            hub.error  = "No se pudo inicializar sesión Rofex."
            return hub

        # --- Catálogo Arbitraje ---
        hub.arb.setup_inicial()   # si falla, arb.catalogo queda vacío pero continuamos

        # --- Catálogo ONs ---
        catalogo = cargar_catalogo_bonos()
        resp = pyRofex.get_all_instruments()
        if resp and resp.get("status") == "OK":
            vivos    = {i["instrumentId"]["symbol"] for i in resp["instruments"]}
            validado = {k: v for k, v in catalogo.items() if k in vivos}
            for t in TICKERS_MEP:
                if t not in validado:
                    validado[t] = {}
            hub.on_engine = MarketManager(validado)

        # --- Una sola suscripción WS ---
        tickers = hub.arb.get_tickers_suscripcion()
        if hub.on_engine:
            tickers = list(set(tickers + hub.on_engine.get_tickers_suscripcion()))
        WebSocketManager(hub).iniciar_ws(tickers)

        hub.status = "running"

    except Exception as e:
        hub.status = "error"
        hub.error  = str(e)

    return hub


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

    /* Tabla de mercado */
    .mkt-table { width: 100%; border-collapse: collapse; font-size: 13px; }
    .mkt-table th {
        color: #555; font-size: 11px; padding: 5px 8px;
        border-bottom: 1px solid #222; text-align: right;
    }
    .mkt-table th.mkt-th-left { text-align: left; }
    .mkt-table td { padding: 4px 8px; border-bottom: 1px solid #1a1a1a; }
    .mkt-table tr:hover td { background: #161b22; }
    .mkt-ticker  { color: #4DA8DA; font-weight: bold; font-size: 13px; }
    .mkt-total   { text-align: right; color: #f0c040; font-weight: bold; }
    .mkt-buy     { text-align: right; color: #00cc66; font-weight: bold; }
    .mkt-sell    { text-align: right; color: #ff4444; font-weight: bold; }
    .mkt-spread  { text-align: right; color: #ccc; }
    .mkt-bar     { text-align: left; font-size: 11px; }
    .mkt-imb-pos { text-align: right; color: #00cc66; font-weight: bold; }
    .mkt-imb-neg { text-align: right; color: #ff4444; font-weight: bold; }
    .mkt-imb-neu { text-align: right; color: #aaa; }
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
        ["Libro", "Opciones", "Estrategias Opciones", "Mercado", "Carteras", "Arbitraje CI/24", "ONs"],
        label_visibility="collapsed"
    )

# Contenedor principal — se reemplaza atómicamente en cada rerun, evitando HTML fantasma
_main = st.empty()


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
# HELPERS DE RENDER - MERCADO
# ==========================================
def render_mercado_table(snaps):
    """Tabla resumen de todos los tickers ordenados por total_money desc."""
    if not snaps:
        return "<p style='color:#888'>Sin datos de mercado. ¿El motor está corriendo?</p>"

    rows = []
    for snap in snaps:
        ticker  = snap.get("ticker", "")
        name    = short_name(ticker)
        m       = snap.get("metrics", {})

        total      = m.get("total_money", 0) or 0
        buy        = m.get("buy_money",   0) or 0
        sell       = m.get("sell_money",  0) or 0
        spread     = m.get("spread",      0) or 0
        imb        = m.get("imbalance",   0) or 0
        last_price = m.get("last_price",  0) or 0
        open_price = m.get("open_price",  0) or 0
        vwap       = m.get("vwap",        0) or 0

        if total == 0:
            continue

        # Barra buy/sell proporcional
        if (buy + sell) > 0:
            buy_blocks  = int((buy  / (buy + sell)) * 10)
            sell_blocks = 10 - buy_blocks
            bar = (f"<span style='color:#00cc66'>{'█' * buy_blocks}</span>"
                   f"<span style='color:#ff4444'>{'█' * sell_blocks}</span>")
        else:
            bar = "<span style='color:#333'>──────────</span>"

        # Color del imbalance
        if imb > 0.05:
            imb_css = "mkt-imb-pos"
        elif imb < -0.05:
            imb_css = "mkt-imb-neg"
        else:
            imb_css = "mkt-imb-neu"

        # Intraday % = last / open - 1
        if open_price > 0 and last_price > 0:
            intraday = last_price / open_price - 1
            intraday_color = "#00cc66" if intraday >= 0 else "#ff4444"
            intraday_str = f"<span style='color:{intraday_color};font-weight:bold'>{intraday:+.2%}</span>"
        else:
            intraday_str = "<span style='color:#555'>-</span>"

        last_str = f"{last_price:,.2f}" if last_price > 0 else "-"
        vwap_str = f"{vwap:,.2f}"       if vwap > 0       else "-"
        open_str = f"{open_price:,.2f}" if open_price > 0 else "-"

        rows.append(f"""<tr>
            <td class='mkt-ticker'>{name}</td>
            <td class='mkt-total'>{fmt_money(total)}</td>
            <td class='mkt-buy'>{fmt_money(buy)}</td>
            <td class='mkt-sell'>{fmt_money(sell)}</td>
            <td class='mkt-bar'>{bar}</td>
            <td class='mkt-spread' style='text-align:right;color:#aaa'>{open_str}</td>
            <td class='mkt-spread' style='text-align:right;color:#ccc'>{last_str}</td>
            <td class='mkt-spread' style='text-align:right;color:#888'>{vwap_str}</td>
            <td style='text-align:right'>{intraday_str}</td>
            <td class='{imb_css}'>{imb:.2%}</td>
        </tr>""")

    if not rows:
        return "<p style='color:#888'>Todos los tickers sin volumen aún.</p>"

    return f"""
    <div class='section-title'>RESUMEN DE MERCADO</div>
    <table class='mkt-table'>
        <thead><tr>
            <th class='mkt-th-left'>TICKER</th>
            <th>TOTAL $</th>
            <th>BUY $</th>
            <th>SELL $</th>
            <th class='mkt-th-left' style='padding-left:8px'>B / S</th>
            <th>OPEN</th>
            <th>LAST</th>
            <th>VWAP</th>
            <th>INTRADAY</th>
            <th>IMBALANCE</th>
        </tr></thead>
        <tbody>{''.join(rows)}</tbody>
    </table>"""


# ==========================================
# HELPERS DE RENDER - OPCIONES
# ==========================================
def fmt_vol(v):
    if not v or v == 0: return "-"
    if v >= 1_000_000: return f"{v/1_000_000:.1f}M"
    if v >= 1_000:     return f"{v/1_000:.0f}k"
    return f"{v:.0f}"


def render_cadena_opciones(docs, spot):
    """Construye la tabla cadena CALL | STRIKE | PUT — idéntica a la terminal."""
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
        if v is None or v == 0: return "-"
        return f"{v:{fmt}}"

    def iv_str(v):
        if v is None or v == 0: return "-"
        return f"{v*100:.1f}%"

    rows = []
    for K in sorted(por_strike.keys()):
        c = por_strike[K].get('CALL', {})
        p = por_strike[K].get('PUT', {})

        c_mid = (c.get('bid', 0) + c.get('offer', 0)) / 2 if c.get('bid', 0) > 0 and c.get('offer', 0) > 0 else c.get('last', 0)
        p_mid = (p.get('bid', 0) + p.get('offer', 0)) / 2 if p.get('bid', 0) > 0 and p.get('offer', 0) > 0 else p.get('last', 0)
        if c_mid == 0 and p_mid == 0:
            continue

        itm_call = spot > 0 and K < spot
        itm_put  = spot > 0 and K > spot
        css_c = "opt-itm" if itm_call else "opt-otm"
        css_p = "opt-itm" if itm_put  else "opt-otm"

        c_h, c_l = c.get('high', 0) or 0, c.get('low', 0) or 0
        p_h, p_l = p.get('high', 0) or 0, p.get('low', 0) or 0
        c_hl = f"{c_h:.1f}/{c_l:.1f}" if c_h > 0 else "-"
        p_hl = f"{p_h:.1f}/{p_l:.1f}" if p_h > 0 else "-"

        rows.append(f"""
        <tr>
            <td class='{css_c}' style='text-align:right;color:#4da8da'>{fmt_vol(c.get('ev'))}</td>
            <td class='{css_c}' style='text-align:center;color:#888;font-size:11px'>{c_hl}</td>
            <td class='{css_c} c-delta'>{cell(c.get('delta'), '.3f')}</td>
            <td class='{css_c} c-iv'>{iv_str(c.get('iv'))}</td>
            <td class='{css_c} c-bid'>{cell(c.get('bid'))}</td>
            <td class='{css_c} c-offer'>{cell(c.get('offer'))}</td>
            <td class='opt-strike'>{K:,.1f}</td>
            <td class='{css_p} c-bid'>{cell(p.get('bid'))}</td>
            <td class='{css_p} c-offer'>{cell(p.get('offer'))}</td>
            <td class='{css_p} c-iv'>{iv_str(p.get('iv'))}</td>
            <td class='{css_p} c-delta'>{cell(p.get('delta'), '.3f')}</td>
            <td class='{css_p}' style='text-align:center;color:#888;font-size:11px'>{p_hl}</td>
            <td class='{css_p}' style='text-align:left;color:#4da8da'>{fmt_vol(p.get('ev'))}</td>
        </tr>""")

    return f"""
    <div class='section-title'>CADENA DE OPCIONES GGAL — SPOT: {'${:,.2f}'.format(spot) if spot else 'N/A'}</div>
    <table class='opt-table'>
        <thead>
            <tr>
                <th colspan='6' style='text-align:center;color:#00cc66'>CALL</th>
                <th style='text-align:center;color:#f0c040'>STRIKE</th>
                <th colspan='6' style='text-align:center;color:#ff4444'>PUT</th>
            </tr>
            <tr>
                <th style='text-align:right'>VOL $</th>
                <th style='text-align:center'>H/L</th>
                <th style='text-align:right'>Delta</th>
                <th style='text-align:right'>IV</th>
                <th style='text-align:right'>Bid</th>
                <th style='text-align:right'>Offer</th>
                <th></th>
                <th style='text-align:right'>Bid</th>
                <th style='text-align:right'>Offer</th>
                <th style='text-align:right'>IV</th>
                <th style='text-align:right'>Delta</th>
                <th style='text-align:center'>H/L</th>
                <th style='text-align:left'>VOL $</th>
            </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
    </table>"""


def render_estrategias(docs_map):
    """Tabla de estrategias idéntica a la terminal. docs_map: {symbol: doc}"""
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
            qty = pata['ratio']
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

        if valida:
            color      = "#ff4444" if neto > 0 else "#00cc66"
            f_val      = abs(s_c - s_v) - neto if (s_c > 0 and s_v > 0 and neto > 0) else 0
            ratio_str  = f"{(f_val/neto)*100:.1f}%" if (f_val > 0 and neto > 0) else "-"
            finish_str = f"${f_val:.2f}" if f_val > 0 else "-"
            rows.append(f"""<tr>
                <td style='padding:2px 6px;color:#ccc'>{estr['nombre']}</td>
                <td style='padding:2px 6px;text-align:right;color:{color}'>${neto:.2f}</td>
                <td style='padding:2px 6px;text-align:right;color:#aaa'>{finish_str}</td>
                <td style='padding:2px 6px;text-align:right;color:#aaa'>{ratio_str}</td>
                <td style='padding:2px 6px;text-align:right;color:#7eb8f7'>{d_net:.3f}</td>
                <td style='padding:2px 6px;text-align:right;color:#aaa'>{g_net:.4f}</td>
                <td style='padding:2px 6px;text-align:right;color:#aaa'>{t_net:.2f}</td>
            </tr>""")
        else:
            rows.append(f"""<tr>
                <td style='padding:2px 6px;color:#555'>{estr['nombre']}</td>
                <td colspan='6' style='padding:2px 6px;color:#555;text-align:center'>Sin Liq</td>
            </tr>""")

    if not rows:
        return ""
    return f"""
    <br>
    <div class='section-title'>ESTRATEGIAS</div>
    <table style='width:100%;border-collapse:collapse;font-size:12px'>
        <thead><tr>
            <th style='text-align:left;color:#555;font-size:11px;padding:3px 6px;border-bottom:1px solid #222'>ESTRATEGIA</th>
            <th style='text-align:right;color:#555;font-size:11px;padding:3px 6px;border-bottom:1px solid #222'>COSTO</th>
            <th style='text-align:right;color:#555;font-size:11px;padding:3px 6px;border-bottom:1px solid #222'>FINISH</th>
            <th style='text-align:right;color:#555;font-size:11px;padding:3px 6px;border-bottom:1px solid #222'>RATIO</th>
            <th style='text-align:right;color:#555;font-size:11px;padding:3px 6px;border-bottom:1px solid #222'>DELTA</th>
            <th style='text-align:right;color:#555;font-size:11px;padding:3px 6px;border-bottom:1px solid #222'>GAMMA</th>
            <th style='text-align:right;color:#555;font-size:11px;padding:3px 6px;border-bottom:1px solid #222'>THETA</th>
        </tr></thead>
        <tbody>{''.join(rows)}</tbody>
    </table>"""


# ==========================================
# VISTA: LIBRO
# ==========================================
if vista == "Libro":
    with _main.container():
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

        time.sleep(0.5)
        st.rerun()


# ==========================================
# VISTA: OPCIONES
# ==========================================
elif vista == "Opciones":
    with _main.container():
        db_op = get_db_opciones()

        st.markdown("## 📊 ACAQuant | Opciones GGAL")

        docs = list(db_op["OptionsSnapshot"].find({}))

        if docs:
            ultimo_ts = max((d.get("updated_at") for d in docs if d.get("updated_at")), default=None)
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
            docs_map = {d['symbol']: d for d in docs if d.get('symbol')}
            st.markdown(render_cadena_opciones(docs, spot), unsafe_allow_html=True)

        time.sleep(0.5)
        st.rerun()


# ==========================================
# VISTA: ESTRATEGIAS OPCIONES
# ==========================================
elif vista == "Estrategias Opciones":
    with _main.container():
        db_op = get_db_opciones()

        st.markdown("## 🧮 ACAQuant | Estrategias Opciones GGAL")

        docs = list(db_op["OptionsSnapshot"].find({}))

        if docs:
            ultimo_ts = max((d.get("updated_at") for d in docs if d.get("updated_at")), default=None)
            if ultimo_ts:
                lag = (datetime.now() - ultimo_ts).total_seconds()
                lag_color = "#ff4444" if lag > 10 else "#00cc66"
                st.markdown(
                    f"<div style='font-size:12px;color:#555;margin-top:-10px'>"
                    f"Última actualización: <span style='color:{lag_color}'>"
                    f"{ultimo_ts.strftime('%H:%M:%S')} ({lag:.1f}s atrás)</span></div>",
                    unsafe_allow_html=True
                )

        st.divider()

        if not docs:
            st.warning("Sin datos de opciones. ¿El motor de opciones está corriendo?")
        else:
            docs_map = {d['symbol']: d for d in docs if d.get('symbol')}
            html = render_estrategias(docs_map)
            if html:
                st.markdown(html, unsafe_allow_html=True)
            else:
                st.info("Sin estrategias disponibles.")

        time.sleep(0.5)
        st.rerun()


# ==========================================
# VISTA: MERCADO
# ==========================================
elif vista == "Mercado":
    with _main.container():
        db = get_db()

        st.markdown("## 🏦 ACAQuant | Mercado")

        all_snaps = list(db["MarketSnapshot"].find({}))

        if all_snaps:
            ultimo_ts = max(
                (s.get("updated_at") for s in all_snaps if s.get("updated_at")),
                default=None
            )
            if ultimo_ts:
                lag = (datetime.now() - ultimo_ts).total_seconds()
                lag_color = "#ff4444" if lag > 5 else "#00cc66"
                st.markdown(
                    f"<div style='font-size:12px;color:#555;margin-top:-10px'>"
                    f"Última actualización: <span style='color:{lag_color}'>"
                    f"{ultimo_ts.strftime('%H:%M:%S')} ({lag:.1f}s atrás)</span></div>",
                    unsafe_allow_html=True
                )

        st.divider()

        all_snaps.sort(
            key=lambda s: s.get("metrics", {}).get("total_money", 0) or 0,
            reverse=True
        )

        st.markdown(render_mercado_table(all_snaps), unsafe_allow_html=True)

        time.sleep(0.5)
        st.rerun()


# ==========================================
# VISTA: CARTERAS
# ==========================================
elif vista == "Carteras":
    with _main.container():
        db_val = get_db_valuaciones()

        st.markdown("## 💼 ACAQuant | Carteras")

        docs   = list(db_val["Carteras"].find({}, {"_id": 0}))
        assets = {a["unidad"]: a for a in db_val["Assets"].find({}, {"_id": 0}) if a.get("unidad")}

        if not docs:
            st.warning("Sin datos de carteras. ¿El cron de main_carteras está configurado?")
        else:
            ultimo_update = next((d.get("actualizado") for d in docs if d.get("actualizado")), None)
            if ultimo_update:
                st.caption(f"Última actualización: {ultimo_update}")

            carteras_disponibles = sorted(set(
                assets.get(d.get("unidad", ""), {}).get("CARTERA", "")
                for d in docs
                if assets.get(d.get("unidad", ""), {}).get("CARTERA", "")
            ))

            CUENTAS = sorted(set(d.get("id_cuenta", "") for d in docs if d.get("id_cuenta")))
            col_radio, col_filtro = st.columns([3, 1])
            with col_radio:
                cuenta_sel = st.radio(
                    "Cuenta",
                    options=[f"Cuenta {c}" for c in CUENTAS] + ["Todas"],
                    horizontal=True,
                    label_visibility="collapsed",
                )
            with col_filtro:
                carteras_sel = st.multiselect(
                    "Cartera",
                    options=carteras_disponibles,
                    default=[],
                    placeholder="Filtrar cartera...",
                    label_visibility="collapsed",
                )

            st.divider()

            if carteras_sel:
                docs_filtrados = [
                    d for d in docs
                    if assets.get(d.get("unidad", ""), {}).get("CARTERA", "") in carteras_sel
                ]
            else:
                docs_filtrados = docs

            def _vto_sort_key(f):
                vto = assets.get(f.get("unidad", ""), {}).get("VENCIMIENTO", "")
                vto_str = str(vto).strip()
                if not vto_str or vto_str.upper() == "NO APLICA":
                    return "9999-99-99"
                return vto_str.split(" ")[0]

            def render_tabla_enriquecida(filas, mostrar_cuenta=False):
                if not filas:
                    st.write("Sin posiciones.")
                    return

                filas = sorted(filas, key=_vto_sort_key)

                rows = []
                for f in filas:
                    unidad  = f.get("unidad", "")
                    asset   = assets.get(unidad, {})
                    vto_raw = asset.get("VENCIMIENTO", "")
                    vto     = str(vto_raw).split(" ")[0] if vto_raw and str(vto_raw).strip().upper() not in ("", "NO APLICA") else ""
                    try:
                        cantidad = int(float(f.get("cantidad") or 0))
                    except (TypeError, ValueError):
                        cantidad = None
                    try:
                        precio = float(f.get("precio")) if f.get("precio") else None
                    except (TypeError, ValueError):
                        precio = None

                    row = {}
                    if mostrar_cuenta:
                        row["CUENTA"] = str(f.get("id_cuenta", ""))
                    row["TICKER"]      = asset.get("TICKER", unidad) or unidad
                    row["EMISOR"]      = asset.get("EMISOR", "") or ""
                    row["VENCIMIENTO"] = vto
                    row["CLASE ACTIVO"]  = asset.get("CLASE_ACTIVO", "") or ""
                    row["CALIFICACIÓN"]  = asset.get("CALIFICACION", "") or ""
                    row["CARTERA"]       = asset.get("CARTERA", "") or ""
                    row["CANTIDAD"]      = cantidad
                    row["PRECIO"]        = precio
                    rows.append(row)

                df = pd.DataFrame(rows)
                col_cfg = {
                    "CANTIDAD": st.column_config.NumberColumn("CANTIDAD", format="%d"),
                    "PRECIO":   st.column_config.NumberColumn("PRECIO",   format="%.4f"),
                }
                st.dataframe(df, use_container_width=True, hide_index=True, column_config=col_cfg)

            if cuenta_sel == "Todas":
                st.caption(f"{len(docs_filtrados)} posiciones totales")
                render_tabla_enriquecida(docs_filtrados, mostrar_cuenta=True)
            else:
                cuenta_id    = cuenta_sel.replace("Cuenta ", "")
                filas_cuenta = [d for d in docs_filtrados if str(d.get("id_cuenta", "")) == cuenta_id]
                st.caption(f"{len(filas_cuenta)} posiciones")
                render_tabla_enriquecida(filas_cuenta)

        time.sleep(0.5)
        st.rerun()


# ==========================================
# VISTA: ARBITRAJE CI/24
# ==========================================
elif vista == "Arbitraje CI/24":
    with _main.container():
        hub = get_engine_hub()

        st.markdown("## ⚖️ ACAQuant | Arbitraje CI / 24hs")

        if hub.status == "error":
            st.error(f"Error al conectar: {hub.error}")
            time.sleep(2)
            st.rerun()
        elif not hub.is_alive():
            st.info("Conectando al WebSocket de Rofex...")
            time.sleep(1)
            st.rerun()
        else:
            rows = hub.arb.get_snapshot_data()

            # Badge de fondeo
            tna       = hub.arb.tna_caucion_offer
            dias      = hub.arb.caucion_dias
            positivos = sum(1 for r in rows if r.get("pnl", 0) > 0)
            st.markdown(
                f"<div style='font-size:13px;color:#888;margin-bottom:4px'>"
                f"Fondeo: <b style='color:#ff6b6b'>{tna:.2f}% TNA ({dias}D)</b>"
                f"&nbsp;&nbsp;|&nbsp;&nbsp;Pares con PNL &gt; 0: "
                f"<b style='color:#00cc66'>{positivos}</b>"
                f"&nbsp;&nbsp;|&nbsp;&nbsp;Total en pantalla: <b style='color:#ccc'>{len(rows)}</b>"
                f"</div>",
                unsafe_allow_html=True
            )
            st.divider()

            if not rows:
                st.markdown(
                    "<p style='color:#555;font-size:13px'>Sin liquidez en ambas puntas aún.</p>",
                    unsafe_allow_html=True
                )
            else:
                th = "".join(
                    f"<th style='text-align:{a};color:#555;font-size:11px;padding:5px 10px;"
                    f"border-bottom:1px solid #222'>{c}</th>"
                    for c, a in [
                        ("ASSET", "left"), ("OFFER CI", "right"), ("BID 24HS", "right"),
                        ("SIZE", "right"), ("REND. DIR.", "right"), ("PNL NETO ($)", "right"),
                    ]
                )
                tbody = ""
                for r in rows:
                    pnl     = r.get("pnl", 0)
                    pcolor  = "#00cc66" if pnl > 0 else "#888"
                    pweight = "bold" if pnl > 0 else "normal"
                    tbody += (
                        f"<tr style='border-bottom:1px solid #1a1a1a'>"
                        f"<td style='padding:4px 10px;color:#4DA8DA;font-weight:bold'>{r.get('asset','')}</td>"
                        f"<td style='padding:4px 10px;text-align:right;color:#ff4444;font-weight:bold'>"
                        f"${r.get('offer_ci', 0):,.2f}</td>"
                        f"<td style='padding:4px 10px;text-align:right;color:#00cc66;font-weight:bold'>"
                        f"${r.get('bid_24', 0):,.2f}</td>"
                        f"<td style='padding:4px 10px;text-align:right;color:#ccc'>"
                        f"{r.get('size', 0):,}</td>"
                        f"<td style='padding:4px 10px;text-align:right;color:#f0c040'>"
                        f"{r.get('rend_directo', 0):.4f}%</td>"
                        f"<td style='padding:4px 10px;text-align:right;"
                        f"color:{pcolor};font-weight:{pweight}'>${pnl:,.2f}</td>"
                        f"</tr>"
                    )
                st.markdown(
                    f"<table style='width:100%;border-collapse:collapse;font-size:13px'>"
                    f"<thead><tr>{th}</tr></thead><tbody>{tbody}</tbody></table>",
                    unsafe_allow_html=True
                )

        time.sleep(0.5)
        st.rerun()


# ==========================================
# VISTA: ONs (Yield Screener)
# ==========================================
elif vista == "ONs":
    with _main.container():
        hub = get_engine_hub()

        h_col, info_col = st.columns([3, 2])
        with h_col:
            st.markdown("## 📊 ACAQuant | Yield Screener (O.N.)")

        if hub.status == "error":
            st.error(f"Error al conectar: {hub.error}")
            time.sleep(2)
            st.rerun()
        elif not hub.is_alive():
            st.info("Conectando al WebSocket de Rofex...")
            time.sleep(1)
            st.rerun()
        elif hub.on_engine is None:
            st.warning("Motor de ONs no disponible (catálogo vacío).")
        else:
            mep    = hub.on_engine.get_mep_dinamico()
            rows   = hub.on_engine.get_snapshot()

            with info_col:
                mep_str = f"${mep:,.2f}" if mep > 0 else "Calculando..."
                st.markdown(
                    f"<div style='font-size:13px;color:#888;margin-top:18px;text-align:right'>"
                    f"MEP: <b style='color:#ff6b6b'>{mep_str}</b>"
                    f"&nbsp;&nbsp;|&nbsp;&nbsp;<b style='color:#ccc'>{len(rows)} ONs</b></div>",
                    unsafe_allow_html=True
                )

            st.divider()

            if not rows:
                st.markdown(
                    "<p style='color:#555;font-size:13px'>Aguardando precios...</p>",
                    unsafe_allow_html=True
                )
            else:
                cols_cfg = [
                    ("TICKER", "left"), ("EMISOR", "left"), ("VENCE", "center"),
                    ("MON", "center"), ("VOL BID ($)", "right"), ("BID PX", "right"),
                    ("TIR BID", "right"), ("TIR OFF", "right"),
                    ("OFF PX", "right"), ("VOL OFF ($)", "right"),
                ]
                th = "".join(
                    f"<th style='text-align:{a};color:#555;font-size:11px;padding:5px 10px;"
                    f"border-bottom:1px solid #222'>{c}</th>"
                    for c, a in cols_cfg
                )
                tbody = ""
                for r in rows:
                    tir_b = f"{r['tir_bid']:.2f}%" if r.get("tir_bid") is not None else "---"
                    tir_o = f"{r['tir_off']:.2f}%" if r.get("tir_off") is not None else "---"
                    tbody += (
                        f"<tr style='border-bottom:1px solid #1a1a1a'>"
                        f"<td style='padding:4px 10px;color:#4DA8DA;font-size:11px'>{r.get('ticker','')}</td>"
                        f"<td style='padding:4px 10px;color:#aaa'>{r.get('emisor','')}</td>"
                        f"<td style='padding:4px 10px;text-align:center;color:#ccc'>{r.get('vence','')}</td>"
                        f"<td style='padding:4px 10px;text-align:center;color:#7eb8f7'>{r.get('moneda','')}</td>"
                        f"<td style='padding:4px 10px;text-align:right;color:#00cc66'>"
                        f"{fmt_money(r.get('vol_bid', 0))}</td>"
                        f"<td style='padding:4px 10px;text-align:right;color:#00cc66;font-weight:bold'>"
                        f"${r.get('px_bid', 0):,.2f}</td>"
                        f"<td style='padding:4px 10px;text-align:right;color:#f0c040;font-weight:bold'>"
                        f"{tir_b}</td>"
                        f"<td style='padding:4px 10px;text-align:right;color:#f0c040;font-weight:bold'>"
                        f"{tir_o}</td>"
                        f"<td style='padding:4px 10px;text-align:right;color:#ff4444;font-weight:bold'>"
                        f"${r.get('px_off', 0):,.2f}</td>"
                        f"<td style='padding:4px 10px;text-align:right;color:#ff4444'>"
                        f"{fmt_money(r.get('vol_off', 0))}</td>"
                        f"</tr>"
                    )
                st.markdown(
                    f"<table style='width:100%;border-collapse:collapse;font-size:13px'>"
                    f"<thead><tr>{th}</tr></thead><tbody>{tbody}</tbody></table>",
                    unsafe_allow_html=True
                )

        time.sleep(0.5)
        st.rerun()
