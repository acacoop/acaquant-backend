import os
import time
import threading
import pandas as pd
import streamlit as st
from datetime import datetime, timezone
from collections import deque

# --- TUS MANAGERS DE INFRAESTRUCTURA ---
from session_manager import inicializar_sesion
from websocket_manager import WebSocketManager
from mongo_manager import get_mongo_client

# --- CONFIGURACIÓN DE PÁGINA Y TICKERS ---
st.set_page_config(layout="wide", page_title="ACAQuant - Mesa", page_icon="📈")

TICKERS = [
    "MERV - XMEV - TZXM6 - 24hs", "MERV - XMEV - S17A6 - 24hs",
    "MERV - XMEV - S30A6 - 24hs", "MERV - XMEV - S29Y6 - 24hs", "MERV - XMEV - T30J6 - 24hs",
    "MERV - XMEV - S31L6 - 24hs", "MERV - XMEV - S31G6 - 24hs", "MERV - XMEV - S30O6 - 24hs",
    "MERV - XMEV - S30N6 - 24hs", "MERV - XMEV - T15E7 - 24hs", "MERV - XMEV - T30A7 - 24hs",
    "MERV - XMEV - T31Y7 - 24hs", "MERV - XMEV - T30J7 - 24hs"
]

# --- ESTILOS VISUALES (TU CSS ORIGINAL) ---
st.markdown("""
    <style>
    .section-header { font-size: 18px; font-weight: bold; color: #4DA8DA; margin-bottom: 10px; border-bottom: 1px solid #333; padding-bottom: 5px; }
    .metric-value { font-size: 24px; font-weight: bold; color: #E5E5E5; }
    </style>
""", unsafe_allow_html=True)

# =====================================================================
# BLOQUE 1: INICIALIZACIÓN (MÚLTIPLES LECTORES, CERO ESCRITURAS)
# =====================================================================
if "initialized" not in st.session_state:
    # 1A. Estructura de memoria RAM visual para el trader
    st.session_state["market_data"] = {
        t: {
            "book": {"bids": [], "offers": []},
            "trades": deque(maxlen=100),
            "stats": {"total_vol": 0.0, "buy_vol": 0.0, "sell_vol": 0.0}
        } for t in TICKERS
    }

    # 1B. LECTURA HISTÓRICA DE MONGODB (Solo al abrir la página)
    try:
        mongo_client = get_mongo_client()
        col_trades = mongo_client["Trading"]["TimeSales"]
        hoy = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        for ticker in TICKERS:
            # Trae las operaciones de hoy desde las 00:00 para armar el acumulado
            cursor = col_trades.find({"ticker": ticker, "timestamp": {"$gte": hoy}}).sort("timestamp", 1)
            for doc in cursor:
                px, sz, side = doc.get("price", 0), doc.get("size", 0), doc.get("side", "MID")
                cash = doc.get("money", (px / 100.0) * sz)  # Fallback por si no viene el campo money

                # Suma al acumulado del día
                st.session_state["market_data"][ticker]["stats"]["total_vol"] += cash
                if side == "BUY":
                    st.session_state["market_data"][ticker]["stats"]["buy_vol"] += cash
                elif side == "SELL":
                    st.session_state["market_data"][ticker]["stats"]["sell_vol"] += cash

                # Carga los últimos trades para la cinta visual
                st.session_state["market_data"][ticker]["trades"].appendleft({
                    "timestamp": doc["timestamp"], "price": px, "size": sz, "side": side
                })
    except Exception as e:
        print(f"Error conectando a Mongo en Lector Web: {e}")


    # =====================================================================
    # BLOQUE 2: MOTOR WEBSOCKET EN VIVO (SOLO PARA PANTALLA)
    # =====================================================================
    class StreamlitWSEngine:
        def procesar_mensaje(self, msg):
            if msg.get("type") != "Md": return
            ticker = msg["instrumentId"]["symbol"]
            if ticker not in st.session_state["market_data"]: return

            data = msg["marketData"]
            st_data = st.session_state["market_data"][ticker]

            # A. Actualiza el Order Book en vivo (No pasa por Mongo)
            if "BI" in data: st_data["book"]["bids"] = data["BI"][:5]
            if "OF" in data: st_data["book"]["offers"] = data["OF"][:5]

            # B. Actualiza la Cinta y el Volumen al instante
            if "LA" in data:
                px, sz = float(data["LA"]["price"]), data["LA"]["size"]
                # Logica visual rapida de agresor
                b1 = st_data["book"]["bids"][0]["price"] if st_data["book"]["bids"] else 0
                side = "BUY" if px > b1 else "SELL"
                cash = (px / 100.0) * sz

                st_data["stats"]["total_vol"] += cash
                if side == "BUY":
                    st_data["stats"]["buy_vol"] += cash
                else:
                    st_data["stats"]["sell_vol"] += cash

                st_data["trades"].appendleft({
                    "timestamp": datetime.now(), "price": px, "size": sz, "side": side
                })


    # 2B. Conexión real a Rofex
    if inicializar_sesion():
        engine_ws = StreamlitWSEngine()
        ws_manager = WebSocketManager(engine_ws)
        ws_manager.iniciar_ws(TICKERS, depth=5)

    st.session_state["initialized"] = True

# =====================================================================
# BLOQUE 3: INTERFAZ GRÁFICA (TU DISEÑO DE BLOQUES)
# =====================================================================
ticker_seleccionado = st.selectbox("Seleccionar Activo", TICKERS)
v = st.session_state["market_data"][ticker_seleccionado]

# Fila superior: Métricas
col1, col2, col3 = st.columns(3)
col1.metric("Volumen Total", f"${v['stats']['total_vol']:,.0f}")
col2.metric("Buy Flow", f"${v['stats']['buy_vol']:,.0f}")
col3.metric("Sell Flow", f"${v['stats']['sell_vol']:,.0f}")

st.markdown("---")
col_tape, col_book = st.columns([1.5, 1])

# Columna Izquierda: Time & Sales
with col_tape:
    st.markdown('<div class="section-header">BOOK (TIME & SALES)</div>', unsafe_allow_html=True)
    df_t = pd.DataFrame(v["trades"], columns=['timestamp', 'price', 'size', 'side'])
    if not df_t.empty:
        df_t['timestamp'] = pd.to_datetime(df_t['timestamp']).dt.strftime('%H:%M:%S')
        st.dataframe(
            df_t.style.apply(lambda x: ['color: #00FF00' if x.side == 'BUY' else 'color: #FF0000' for _ in x], axis=1)
            .format({'price': '{:,.2f}', 'size': '{:,.0f}'}),
            hide_index=True, use_container_width=True, height=600
        )

# Columna Derecha: Order Book (Depth)
with col_book:
    st.markdown('<div class="section-header">ORDERS (DEPTH)</div>', unsafe_allow_html=True)
    df_b = pd.DataFrame(v["book"]["bids"], columns=['price', 'size']).rename(
        columns={'price': 'Bid P', 'size': 'Bid Q'})
    df_o = pd.DataFrame(v["book"]["offers"], columns=['price', 'size']).rename(
        columns={'price': 'Ask P', 'size': 'Ask Q'})
    depth_df = pd.concat([df_b, df_o], axis=1).fillna("")

    if not depth_df.empty:
        st.dataframe(depth_df, hide_index=True, use_container_width=True, height=600)

# =====================================================================
# BLOQUE 4: MOTOR DE REFRESCO
# =====================================================================
time.sleep(0.4)
st.rerun()