import os
import time
import json
import pyRofex
from session_manager import inicializar_sesion

# Probamos con la base que me pasaste
TICKER_PRUEBA = "MERV - XMEV - GFGC71747A - 24hs"


def raw_market_data_handler(message):
    """
    Imprime el mensaje tal cual llega del servidor.
    Acá vamos a ver si existen las llaves 'OP', 'HI', 'LO', 'EV', etc.
    """
    try:
        data = message.get("marketData", {})

        # Limpiamos consola para ver solo el último paquete
        os.system('cls' if os.name == 'nt' else 'clear')

        print(f"📡 FEED EN VIVO | INSTRUMENTO: {TICKER_PRUEBA}")
        print("=" * 60)

        # Si el diccionario está vacío, avisamos
        if not data:
            print("⚠️ Mensaje recibido pero 'marketData' viene vacío.")
        else:
            print(json.dumps(data, indent=4))

        print("=" * 60)
        print("Buscá las siglas: OP (Open), HI (High), LO (Low), EV (Volumen $) o NV (Nominales)")
        print("Presioná Ctrl+C para salir...")

    except Exception as e:
        print(f"❌ Error: {e}")


def run_test():
    if not inicializar_sesion():
        return

    # 1. Definimos el handler
    pyRofex.add_websocket_market_data_handler(raw_market_data_handler)

    # 2. Iniciamos conexión
    pyRofex.init_websocket_connection()

    # Pausa de seguridad para el handshake
    time.sleep(2)

    # 3. Pedimos TODO el set de datos
    entries = [
        pyRofex.MarketDataEntry.BIDS,
        pyRofex.MarketDataEntry.OFFERS,
        pyRofex.MarketDataEntry.LAST,
        pyRofex.MarketDataEntry.OPENING_PRICE,  # Debería venir como 'OP'
        pyRofex.MarketDataEntry.HIGH_PRICE,  # Debería venir como 'HI'
        pyRofex.MarketDataEntry.LOW_PRICE,  # Debería venir como 'LO'
        pyRofex.MarketDataEntry.NOMINAL_VOLUME,  # Debería venir como 'NV'
        pyRofex.MarketDataEntry.TRADE_EFFECTIVE_VOLUME,  # Debería venir como 'EV'
        pyRofex.MarketDataEntry.TRADE_COUNT  # Debería venir como 'TC'
    ]

    print(f"📡 Suscribiendo a todos los campos para {TICKER_PRUEBA}...")

    pyRofex.market_data_subscription(
        tickers=[TICKER_PRUEBA],
        entries=entries
    )

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pyRofex.close_websocket_connection()


if __name__ == "__main__":
    run_test()