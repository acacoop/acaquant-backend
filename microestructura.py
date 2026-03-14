import os
import time
import threading
from datetime import datetime, timezone
from pymongo import MongoClient
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich import box
import warnings

# --- TUS MANAGERS ---
from session_manager import inicializar_sesion
from websocket_manager import WebSocketManager

warnings.filterwarnings('ignore')

# =====================================================================
# CONFIGURACIÓN Y TICKERS
# =====================================================================
TICKERS = [
    "MERV - XMEV - S16M6 - 24hs", "MERV - XMEV - TZXM6 - 24hs", "MERV - XMEV - S17A6 - 24hs",
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

# --- MONGO DB ---
try:
    client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=2000)
    db = client["Trading"]
    col_trades = db["TimeSales"]
except Exception as e:
    print(f"Error conectando a Mongo: {e}")
    exit()

# --- ESTADOS EN MEMORIA (RAM) ---
market_state = {}
ultima_fecha_procesada = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
ultimo_id_procesado = None


def safe_float(valor):
    try:
        if valor is None or valor == "": return 0.0
        return float(valor)
    except:
        return 0.0


# =====================================================================
# EL ADAPTADOR DEL WEBSOCKET (Inyecta el Level 5 y blinda Nulos)
# =====================================================================
class WSAdapterEngine:
    def update_price(self, ticker, data):
        if ticker not in market_state:
            market_state[ticker] = {
                "total_vol": 0.0, "buy_vol": 0.0, "sell_vol": 0.0,
                "book": {"bids": [], "offers": []}
            }

        state = market_state[ticker]

        # BLINDAJE: Si Rofex manda nulo, lo forzamos a ser una lista vacía
        if "BI" in data:
            state["book"]["bids"] = data["BI"] if data["BI"] is not None else []
        if "OF" in data:
            state["book"]["offers"] = data["OF"] if data["OF"] is not None else []


# =====================================================================
# LÓGICA DE FLUJOS (MONGO)
# =====================================================================
def procesar_trade(doc):
    global ultima_fecha_procesada
    ticker = doc.get("ticker") or doc.get("symbol")
    if not ticker: return

    if ticker not in market_state:
        market_state[ticker] = {
            "total_vol": 0.0, "buy_vol": 0.0, "sell_vol": 0.0,
            "book": {"bids": [], "offers": []}
        }

    state = market_state[ticker]
    price = safe_float(doc.get("price"))
    size = safe_float(doc.get("size"))
    side = doc.get("side", "MID")

    if price > 0 and size > 0:
        dinero = (price / 100.0) * size
        state["total_vol"] += dinero
        if side == "BUY":
            state["buy_vol"] += dinero
        elif side == "SELL":
            state["sell_vol"] += dinero


def sincronizar_con_mongo():
    global ultimo_id_procesado, ultima_fecha_procesada
    query = {"timestamp": {"$gt": ultima_fecha_procesada}}
    if ultimo_id_procesado:
        query = {"_id": {"$gt": ultimo_id_procesado}}

    cursor = col_trades.find(query).sort("_id", 1).limit(5000)
    for doc in cursor:
        procesar_trade(doc)
        ultimo_id_procesado = doc["_id"]


# =====================================================================
# LÓGICA DE MUROS (ORDER BOOK REAL)
# =====================================================================
def obtener_muros_reales():
    muros = {}
    for ticker, state in market_state.items():
        book = state.get("book", {"bids": [], "offers": []})

        # DOBLE BLINDAJE: Nos aseguramos de que bids y offers sean iterables siempre
        bids = book.get('bids') or []
        offers = book.get('offers') or []

        plata_bid = sum((safe_float(lvl.get('price')) / 100.0) * safe_float(lvl.get('size')) for lvl in bids if
                        isinstance(lvl, dict))
        plata_ask = sum((safe_float(lvl.get('price')) / 100.0) * safe_float(lvl.get('size')) for lvl in offers if
                        isinstance(lvl, dict))

        ticker_limpio = ticker.replace("MERV - XMEV - ", "").replace(" - 24hs", "")
        muros[ticker_limpio] = {"bid": plata_bid, "ask": plata_ask}
    return muros


# =====================================================================
# RENDERIZADO VISUAL
# =====================================================================
def generar_tablero():
    # --- PANEL IZQUIERDO: SCREENER DE FLUJOS (Trades Ejecutados) ---
    t_screener = Table(title="[bold #ff9900]SCREENER DE FLUJOS (TRADES HOY)[/]", box=box.SQUARE, border_style="#ff9900")
    t_screener.add_column("Ticker", style="bold white")
    t_screener.add_column("Total Vol ($)", justify="right", style="bold white")
    t_screener.add_column("Net Flow ($)", justify="right")
    t_screener.add_column("Trade Imb.", justify="center")

    filas_screener = []
    for ticker, state in market_state.items():
        if state["total_vol"] == 0: continue
        net_flow = state["buy_vol"] - state["sell_vol"]
        trade_imb = (net_flow / state["total_vol"]) * 100 if state["total_vol"] > 0 else 0
        ticker_limpio = ticker.replace("MERV - XMEV - ", "").replace(" - 24hs", "")
        filas_screener.append(
            {"ticker": ticker_limpio, "total": state["total_vol"], "net": net_flow, "t_imb": trade_imb})

    filas_screener.sort(key=lambda x: x["total"], reverse=True)
    for f in filas_screener[:25]:
        net_color = "bold green" if f["net"] > 0 else "bold red" if f["net"] < 0 else "white"
        timb_color = "bold green" if f["t_imb"] > 20 else "bold red" if f["t_imb"] < -20 else "white"
        t_screener.add_row(f["ticker"], f"${f['total']:,.0f}", f"[{net_color}]${f['net']:,.0f}[/]",
                           f"[{timb_color}]{f['t_imb']:+.1f}%[/]")

    # --- PANEL DERECHO: ORDER BOOK REAL (Puntas Pendientes) ---
    t_book = Table(title="[bold #00ccff]ORDER BOOK L5 (MUROS REALES)[/]", box=box.SQUARE, border_style="#00ccff")
    t_book.add_column("Ticker (Bid)", style="bold green")
    t_book.add_column("Muro Compra ($)", justify="right", style="green")
    t_book.add_column("Ticker (Ask)", style="bold red")
    t_book.add_column("Muro Venta ($)", justify="right", style="red")

    muros_actuales = obtener_muros_reales()
    lista_book = [{"ticker": k, "bid": v["bid"], "ask": v["ask"]} for k, v in muros_actuales.items()]

    top_bids = sorted([x for x in lista_book if x["bid"] > 0], key=lambda x: x["bid"], reverse=True)[:25]
    top_asks = sorted([x for x in lista_book if x["ask"] > 0], key=lambda x: x["ask"], reverse=True)[:25]

    max_filas = max(len(top_bids), len(top_asks), 15)
    for i in range(max_filas):
        b_t = top_bids[i]["ticker"] if i < len(top_bids) else "-"
        b_v = f"${top_bids[i]['bid']:,.0f}" if i < len(top_bids) else "-"
        a_t = top_asks[i]["ticker"] if i < len(top_asks) else "-"
        a_v = f"${top_asks[i]['ask']:,.0f}" if i < len(top_asks) else "-"
        t_book.add_row(b_t, b_v, a_t, a_v)

    layout = Table.grid(padding=4)
    layout.add_row(Panel(t_screener, border_style="#ff9900", expand=False),
                   Panel(t_book, border_style="#00ccff", expand=False))
    return layout


# =====================================================================
# EJECUCIÓN PRINCIPAL
# =====================================================================
def run():
    os.system('cls' if os.name == 'nt' else 'clear')
    print("Iniciando Terminal Quant...")

    # 1. Autenticación
    if not inicializar_sesion():
        return

    # 2. Inicializar histórico (Panel Izquierdo)
    sincronizar_con_mongo()

    # 3. Lanzar WebSocket (Panel Derecho)
    adaptador = WSAdapterEngine()
    ws_manager = WebSocketManager(adaptador)

    if not ws_manager.iniciar_ws(TICKERS, depth=5):
        print("❌ Falló la suscripción WS. Los muros no se actualizarán.")

    # 4. Iniciar UI (Bucle infinito)
    with Live(generar_tablero(), refresh_per_second=2, auto_refresh=False) as live:
        try:
            while True:
                time.sleep(0.5)
                # Mantener la BD fresca
                sincronizar_con_mongo()
                # Repintar
                live.update(generar_tablero(), refresh=True)
        except KeyboardInterrupt:
            ws_manager.cerrar_ws()
            print("\nScreener cerrado.")


if __name__ == "__main__":
    run()