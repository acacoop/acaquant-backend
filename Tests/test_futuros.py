import pyRofex
import time
import json
from session_manager import inicializar_sesion


def global_handler(message):
    """Imprime CUALQUIER mensaje que mande el WebSocket."""
    print(f"\n📩 MENSAJE RECIBIDO: {json.dumps(message, indent=2)}")


def run_debug_sniffer(symbol="GGAL/FEB26"):
    if not inicializar_sesion(): return

    pyRofex.init_websocket_connection()

    # Ponemos el handler para TODOS los tipos de mensajes
    pyRofex.add_websocket_market_data_handler(global_handler)
    pyRofex.add_websocket_order_report_handler(global_handler)
    pyRofex.add_websocket_error_handler(global_handler)

    print(f"📡 Intentando suscribir a: {symbol}...")

    # Guardamos el resultado de la suscripción para ver si Rofex la aceptó
    resp = pyRofex.market_data_subscription(
        tickers=[symbol],
        entries=[
            pyRofex.MarketDataEntry.BIDS,
            pyRofex.MarketDataEntry.OFFERS,
            pyRofex.MarketDataEntry.LAST
        ]
    )
    print(f"📊 Respuesta de suscripción: {json.dumps(resp, indent=2)}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Detenido.")


if __name__ == "__main__":
    run_debug_sniffer()