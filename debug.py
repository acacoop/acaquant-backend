import pyRofex
import time
from datetime import datetime
from pprint import pprint
from session_manager import inicializar_sesion

TICKERS_DEBUG = ["ORO/MAR26", "WTI/MAR26"]


def message_handler(message):
    if message.get("type") == "Md":
        symbol = message["instrumentId"]["symbol"]
        md = message["marketData"]

        print(f"\n--- MENSAJE RECIBIDO DE {symbol} ---")

        # 1. TIMESTAMP DEL PAQUETE (¿Es el del evento o del envío?)
        ts_ms = md.get('timestamp')
        ts_legible = datetime.fromtimestamp(ts_ms / 1000.0).strftime('%H:%M:%S.%f') if ts_ms else "N/A"
        print(f"Timestamp del Mensaje (MD): {ts_legible}")

        # 2. CONTENIDO DEL LAST (LA)
        last_data = md.get('LA')

        if last_data:
            print("DATA EN 'LA' (LAST):")
            pprint(last_data)
            # ¿Viene precio? ¿Viene fecha?
            if 'date' in last_data:
                print(f"Fecha explicita en Last: {datetime.fromtimestamp(last_data['date'] / 1000.0)}")
        else:
            print("CAMPO 'LA' (LAST) VACÍO O NO PRESENTE.")

        # 3. PUNTAS
        if md.get('BI'):
            print(f"Bid: {md['BI'][0]['price']} x {md['BI'][0]['size']}")
        if md.get('OF'):
            print(f"Offer: {md['OF'][0]['price']} x {md['OF'][0]['size']}")

        print("-" * 40)


def run():
    print("🕵️ INICIANDO DEBUGGER DE MENSAJES ROFEX...")

    if not inicializar_sesion():
        return

    pyRofex.init_websocket_connection()
    pyRofex.add_websocket_market_data_handler(message_handler)

    pyRofex.market_data_subscription(
        tickers=TICKERS_DEBUG,
        entries=[
            pyRofex.MarketDataEntry.BIDS,
            pyRofex.MarketDataEntry.OFFERS,
            pyRofex.MarketDataEntry.LAST
        ]
    )

    while True:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            break

    pyRofex.close_websocket_connection()


if __name__ == "__main__":
    run()