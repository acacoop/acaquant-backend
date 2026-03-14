import os
import queue
import pyRofex
import threading
import time
import traceback
import streamlit as st
import pandas as pd
from datetime import datetime
from collections import deque
from pymongo import MongoClient

# --- TUS MANAGERS DE INFRAESTRUCTURA ---
from session_manager import inicializar_sesion
from websocket_manager import WebSocketManager

# --- CONFIGURACIÓN ---
TICKERS = [
    "MERV - XMEV - TZXM6 - 24hs", "MERV - XMEV - S17A6 - 24hs",
    "MERV - XMEV - S30A6 - 24hs", "MERV - XMEV - S29Y6 - 24hs", "MERV - XMEV - T30J6 - 24hs",
    "MERV - XMEV - S31L6 - 24hs", "MERV - XMEV - S31G6 - 24hs", "MERV - XMEV - S30O6 - 24hs",
    "MERV - XMEV - S30N6 - 24hs", "MERV - XMEV - T15E7 - 24hs", "MERV - XMEV - T30A7 - 24hs",
    "MERV - XMEV - T31Y7 - 24hs", "MERV - XMEV - T30J7 - 24hs", "MERV - XMEV - TY30P - 24hs",
    "MERV - XMEV - X15Y6 - 24hs", "MERV - XMEV - X29Y6 - 24hs", "MERV - XMEV - TZX26 - 24hs",
    "MERV - XMEV - X31L6 - 24hs", "MERV - XMEV - TX26 - 24hs", "MERV - XMEV - TZXO6 - 24hs",
    "MERV - XMEV - X30N6 - 24hs", "MERV - XMEV - TZXD6 - 24hs", "MERV - XMEV - TZXM7 - 24hs",
    "MERV - XMEV - TZXY7 - 24hs", "MERV - XMEV - TZX27 - 24hs", "MERV - XMEV - TX28 - 24hs",
    "MERV - XMEV - TZXD7 - 24hs", "MERV - XMEV - TZX28 - 24hs", "MERV - XMEV - DICP - 24hs",
    "MERV - XMEV - PARP - 24hs"
]

VOLUME_BUCKET_SIZES = {
    "MERV - XMEV - TZXM6 - 24hs": 1056003093,
    "MERV - XMEV - S17A6 - 24hs": 1269958754, "MERV - XMEV - S30A6 - 24hs": 205979378,
    "MERV - XMEV - S29Y6 - 24hs": 384357785, "MERV - XMEV - T30J6 - 24hs": 291213720,
    "MERV - XMEV - S31L6 - 24hs": 93799093, "MERV - XMEV - S31G6 - 24hs": 39515420,
    "MERV - XMEV - S30O6 - 24hs": 30698089, "MERV - XMEV - S30N6 - 24hs": 25196577,
    "MERV - XMEV - T15E7 - 24hs": 157290278, "MERV - XMEV - T30A7 - 24hs": 438883807,
    "MERV - XMEV - T31Y7 - 24hs": 98359279, "MERV - XMEV - T30J7 - 24hs": 21615689,
    "MERV - XMEV - TY30P - 24hs": 26615939, "MERV - XMEV - X15Y6 - 24hs": 113153036,
    "MERV - XMEV - X29Y6 - 24hs": 426532800, "MERV - XMEV - TZX26 - 24hs": 363769490,
    "MERV - XMEV - X31L6 - 24hs": 67044105, "MERV - XMEV - TX26 - 24hs": 139822955,
    "MERV - XMEV - TZXO6 - 24hs": 208287938, "MERV - XMEV - X30N6 - 24hs": 80774915,
    "MERV - XMEV - TZXD6 - 24hs": 262315858, "MERV - XMEV - TZXM7 - 24hs": 176877365,
    "MERV - XMEV - TZXY7 - 24hs": 133934, "MERV - XMEV - TZX27 - 24hs": 9495066,
    "MERV - XMEV - TX28 - 24hs": 23072063, "MERV - XMEV - TZXD7 - 24hs": 156324283,
    "MERV - XMEV - TZX28 - 24hs": 172279837, "MERV - XMEV - DICP - 24hs": 27262545,
    "MERV - XMEV - PARP - 24hs": 4436050
}


class MicrostructureEngine:
    def __init__(self, tickers):
        self.tickers = tickers
        self.tick_queue = queue.Queue()
        self.market_state = {
            t: {
                "book": {"bids": [], "offers": []},
                "trades": deque(maxlen=50),
                "last_nv": 0.0, "last_price": 0.0,
                "closed_vpins": deque(maxlen=50),
                "vpin_stats": {"current_buy_vol": 0, "current_sell_vol": 0, "last_vpin": 0.0},
                "daily_financials": {"total_money": 0.0, "buy_money": 0.0, "sell_money": 0.0, "total_nominals": 0.0},
                "top_trades": []
            } for t in self.tickers
        }
        threading.Thread(target=self._worker_loop, daemon=True).start()

    def update_price(self, ticker, data):
        self.tick_queue.put((ticker, data))

    def _worker_loop(self):
        while True:
            try:
                ticker, data = self.tick_queue.get(timeout=0.5)
                self._procesar_tick_logica(ticker, data)
            except queue.Empty:
                pass

    def _procesar_tick_logica(self, ticker, data):
        st_t = self.market_state[ticker]
        if "BI" in data: st_t["book"]["bids"] = data["BI"][:5]
        if "OF" in data: st_t["book"]["offers"] = data["OF"][:5]
        if "EV" in data and data["EV"]: st_t["daily_financials"]["total_money"] = float(data["EV"])
        if "NV" in data and data["NV"]: st_t["daily_financials"]["total_nominals"] = float(data["NV"])

        last, nv = data.get("LA"), data.get("NV")
        if last and nv:
            px, ts_ms = float(last.get("price")), last.get("date")
            if st_t["last_nv"] == 0:
                st_t["last_nv"], st_t["last_price"] = nv, px
                return

            if nv > st_t["last_nv"]:
                sz = nv - st_t["last_nv"];
                st_t["last_nv"] = nv
                b1 = st_t["book"]["bids"][0]["price"] if st_t["book"]["bids"] else 0
                o1 = st_t["book"]["offers"][0]["price"] if st_t["book"]["offers"] else 0
                side = "BUY" if px >= o1 and o1 > 0 else "SELL" if px <= b1 and b1 > 0 else (
                    "BUY" if px > st_t["last_price"] else "SELL")
                st_t["last_price"] = px;
                cash = (px / 100.0) * sz

                # VPIN Logic (Simplificada)
                if side == "BUY":
                    st_t["vpin_stats"]["current_buy_vol"] += sz
                else:
                    st_t["vpin_stats"]["current_sell_vol"] += sz

                dt = datetime.fromtimestamp(ts_ms / 1000)
                trade = {"timestamp": dt, "price": px, "size": sz, "side": side, "money": cash}
                st_t["top_trades"].append(trade)
                st_t["top_trades"] = sorted(st_t["top_trades"], key=lambda x: x["money"], reverse=True)[:15]
                st_t["trades"].appendleft(trade)

    def get_market_view(self, ticker):
        s = self.market_state[ticker]
        b, o, fs, vs = s["book"]["bids"], s["book"]["offers"], s["daily_financials"], s["vpin_stats"]
        m_px, sp, imb = 0.0, 0.0, 0.0
        if b and o:
            m_px = (b[0]['price'] * o[0]['size'] + o[0]['price'] * b[0]['size']) / (b[0]['size'] + o[0]['size'])
            sp = o[0]['price'] - b[0]['price']
            imb = (sum(x['size'] for x in b) - sum(x['size'] for x in o)) / (
                        sum(x['size'] for x in b) + sum(x['size'] for x in o)) if (sum(x['size'] for x in b) + sum(
                x['size'] for x in o)) > 0 else 0

        curr = vs["current_buy_vol"] + vs["current_sell_vol"]
        return {
            "book": s["book"], "trades": list(s["trades"]), "top_trades": s["top_trades"],
            "metrics": {
                "micro_price": m_px, "spread": sp, "imbalance": imb,
                "total_nominals": fs["total_nominals"], "tot_money": fs["total_money"],
                "buy_money": fs["buy_money"], "sell_money": fs["sell_money"],
                "vwap": (fs["total_money"] / fs["total_nominals"] * 100) if fs["total_nominals"] > 0 else 0,
                "vpin_prom": vs["last_vpin"],
                "vpin_vivo": abs(vs["current_buy_vol"] - vs["current_sell_vol"]) / curr if curr > 0 else 0,
                "buy_b": vs["current_buy_vol"], "sell_b": vs["current_sell_vol"]
            }
        }


# ==========================================
# 2. INTERFAZ STREAMLIT (BLOOMBERG PRO)
# ==========================================
st.set_page_config(page_title="TERMINAL", layout="wide", initial_sidebar_state="collapsed")

# CSS Estilo Bloomberg Minimalista
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Roboto+Mono:wght@400;700&display=swap');
    html, body, [class*="css"] { font-family: 'Roboto Mono', monospace; background-color: #000000; color: #E0E0E0; }
    .stSelectbox { max-width: 600px; margin: 0 auto; padding-bottom: 20px; }
    .metric-row { display: flex; justify-content: space-between; padding: 2px 0; border-bottom: 1px solid #1A1A1A; line-height: 1.1; }
    .label { color: #FFA500; font-size: 0.7rem; text-transform: uppercase; }
    .value { font-weight: bold; font-size: 0.85rem; color: #FFFFFF; }
    .bid-text { color: #00FF00 !important; }
    .ask-text { color: #FF0000 !important; }
    [data-testid="stHeader"] { background: rgba(0,0,0,0); }
    [data-testid="stTable"] td, [data-testid="stDataFrame"] td { padding: 0px 4px !important; font-size: 0.75rem !important; }
    .section-header { color: #FFA500; font-size: 0.8rem; border-bottom: 1px solid #333; margin-top: 15px; margin-bottom: 5px; font-weight: bold; }
    </style>
    """, unsafe_allow_html=True)

from session_manager import inicializar_sesion
from websocket_manager import WebSocketManager


@st.cache_resource
def get_system():
    if not inicializar_sesion(): return None
    eng = MicrostructureEngine(TICKERS)
    ws = WebSocketManager(eng);
    ws.iniciar_ws(TICKERS, depth=5)
    return eng


sys = get_system()

if sys:
    # 0. Selector Superior
    ticker = st.selectbox("", TICKERS, label_visibility="collapsed")
    v = sys.get_market_view(ticker);
    m = v["metrics"]

    # --- ESTRUCTURA DE 3 COLUMNAS ---
    col1, col2, col3 = st.columns([1, 1.3, 1.4])

    # COLUMNA 1: ANALYTICS + TOP TRADES
    with col1:
        st.markdown('<div class="section-header">ANALYTICS</div>', unsafe_allow_html=True)


        def draw_m(label, val):
            st.markdown(
                f"<div class='metric-row'><span class='label'>{label}</span><span class='value'>{val}</span></div>",
                unsafe_allow_html=True)


        draw_m("MICRO PX", f"{m['micro_price']:,.4f}")
        draw_m("SPREAD", f"{m['spread']:,.2f}")
        draw_m("IMBALANCE", f"{m['imbalance']:.2%}")
        draw_m("VPIN AVG", f"{m['vpin_prom']:.2%}")
        draw_m("VPIN LIVE", f"{m['vpin_vivo']:.2%}")
        st.markdown(
            f"<div class='metric-row'><span class='label'>B/S BUCKET</span><span><b class='bid-text'>{m['buy_b']:,.0f}</b> / <b class='ask-text'>{m['sell_b']:,.0f}</b></span></div>",
            unsafe_allow_html=True)
        draw_m("VWAP", f"${m['vwap']:,.2f}")
        draw_m("TOTAL $", f"${m['tot_money']:,.0f}")
        draw_m("BUY $", f"${m['buy_money']:,.0f}")
        draw_m("SELL $", f"${m['sell_money']:,.0f}")

        st.markdown('<div class="section-header">TOP TRADES ($)</div>', unsafe_allow_html=True)
        df_w = pd.DataFrame(v["top_trades"], columns=['price', 'money', 'side'])
        if not df_w.empty:
            st.dataframe(df_w.style.format({'price': '{:,.2f}', 'money': '${:,.0f}'})
                         .apply(lambda x: ['color: #00FF00' if x.side == 'BUY' else 'color: #FF0000' for _ in x],
                                axis=1),
                         hide_index=True, use_container_width=True, height=350)

    # COLUMNA 2: BOOK (TAPE CON HORA)
    with col2:
        st.markdown('<div class="section-header">BOOK (TIME & SALES)</div>', unsafe_allow_html=True)
        df_t = pd.DataFrame(v["trades"], columns=['timestamp', 'price', 'size', 'side'])
        if not df_t.empty:
            df_t['timestamp'] = df_t['timestamp'].dt.strftime('%H:%M:%S')
            st.dataframe(
                df_t.style.apply(lambda x: ['color: #00FF00' if x.side == 'BUY' else 'color: #FF0000' for _ in x],
                                 axis=1)
                .format({'price': '{:,.2f}', 'size': '{:,.0f}'}),
                hide_index=True, use_container_width=True, height=750)

    # COLUMNA 3: ORDERS (DEPTH CON COLORES)
    with col3:
        st.markdown('<div class="section-header">ORDERS (DEPTH)</div>', unsafe_allow_html=True)
        df_b = pd.DataFrame(v["book"]["bids"], columns=['price', 'size']).rename(
            columns={'price': 'Bid P', 'size': 'Bid Q'})
        df_o = pd.DataFrame(v["book"]["offers"], columns=['price', 'size']).rename(
            columns={'price': 'Ask P', 'size': 'Ask Q'})

        # Unimos y formateamos con colores fijos para Bid/Ask
        depth_df = pd.concat([df_b, df_o], axis=1)
        st.dataframe(depth_df.style.format("{:,.2f}")
                     .set_properties(subset=['Bid P', 'Bid Q'], **{'color': '#00FF00'})
                     .set_properties(subset=['Ask P', 'Ask Q'], **{'color': '#FF0000'}),
                     hide_index=True, use_container_width=True)

    time.sleep(0.4)
    st.rerun()