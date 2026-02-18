import pyRofex
import time
import json
from session_manager import inicializar_sesion

# --- CONFIGURACIÓN ---
TICKER = "MERV - XMEV - GFGC75054F - 24hs"


# ---------------------

def custom_message_handler(message):
    data = message.get("marketData")
    if data:
        bids = data.get("BI", [])
        offers = data.get("OF", [])

        # Limpiamos pantalla para efecto "Terminal" (opcional)
        # print("\033[H\033[J", end="")

        print("\n" + "—" * 65)
        print(f"📊 DEEP ORDER BOOK (L2): {TICKER}")
        print(f"Timestamp: {time.strftime('%H:%M:%S')} | Depth: {len(bids)}B / {len(offers)}O")
        print("-" * 65)
        print(f"{'CANT (Buy)':<15} | {'BID':^12} || {'OFFER':^12} | {'CANT (Sell)':>15}")
        print("-" * 65)

        # Iteramos sobre todos los niveles de profundidad recibidos
        max_rows = max(len(bids), len(offers))

        for i in range(max_rows):
            # Lado Compra
            b_p = f"{bids[i]['price']:.3f}" if i < len(bids) else "---"
            b_s = f"{int(bids[i]['size']):,}" if i < len(bids) else "---"

            # Lado Venta
            o_p = f"{offers[i]['price']:.3f}" if i < len(offers) else "---"
            o_s = f"{int(offers[i]['size']):,}" if i < len(offers) else "---"

            print(f"{b_s:<15} | {b_p:^12} || {o_p:^12} | {o_s:>15}")

        print("—" * 65)


def custom_error_handler(message):
    print(f"❌ ERROR WS: {message}")


def run_terminal():
    # Inicializamos con tu SessionManager (clave para URLs de tu broker)
    if not inicializar_sesion():
        return

    try:
        # 1. Conexión y Handlers (v0.5.0)
        pyRofex.init_websocket_connection()
        pyRofex.add_websocket_market_data_handler(custom_message_handler)
        pyRofex.add_websocket_error_handler(custom_error_handler)

        print(f"📡 Suscribiendo con profundidad total a {TICKER}...")

        # 2. SUSCRIPCIÓN CON DEPTH
        # Según tu captura image_7a4bbf.png, el entero define la profundidad
        pyRofex.market_data_subscription(
            tickers=[TICKER],
            entries=[
                pyRofex.MarketDataEntry.BIDS,
                pyRofex.MarketDataEntry.OFFERS
            ],
            depth=10  # <--- ACÁ ESTÁ EL CAMBIO CLAVE
        )

        while True:
            time.sleep(1)

    except Exception as e:
        print(f"⚠️ Error en ejecución: {e}")


if __name__ == "__main__":
    run_terminal()