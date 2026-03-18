"""
Script de test para inspeccionar cómo llega el campo OPEN (OPENING_PRICE)
desde el WebSocket de Rofex en tiempo real.

Uso:
    python test_open_field.py

Mostrará el mensaje RAW completo y luego resaltará específicamente
el campo relacionado con el precio de apertura (OP / OPENING_PRICE).
"""

import pyRofex
import time
import json
from datetime import datetime
from session_manager import inicializar_sesion

# ─── CONFIGURACIÓN ────────────────────────────────────────────────
# Podés cambiar estos tickers por los que te interesen inspeccionar
TICKERS_TEST = [
    "MERV - XMEV - GGAL - 24hs",  # subyacente
    "GGAL/ABR26",                  # futuro
]

MAX_MENSAJES = 20   # cuántos mensajes imprimir antes de detenerse
contador = [0]
# ──────────────────────────────────────────────────────────────────


def handler_debug(message):
    """Imprime el mensaje crudo y resalta los campos de apertura."""
    contador[0] += 1
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]

    ticker = message.get('instrumentId', {}).get('symbol', 'DESCONOCIDO')
    data   = message.get('marketData', {})

    print(f"\n{'='*60}")
    print(f"[{ts}] Msg #{contador[0]}  |  Ticker: {ticker}")
    print(f"{'='*60}")

    # ── Mensaje completo (para ver TODOS los campos disponibles)
    print("📦 marketData RAW:")
    print(json.dumps(data, indent=2, default=str))

    # ── Campos específicos de apertura (distintos nombres posibles)
    campos_open = ['OP', 'OV', 'OPENING_PRICE', 'open', 'openingPrice']
    print("\n🔍 Campos relacionados con OPEN encontrados:")
    encontrado = False
    for campo in campos_open:
        if campo in data:
            print(f"   ✅  data['{campo}'] = {data[campo]}")
            encontrado = True
    if not encontrado:
        print("   ⚠️  Ninguno de los campos esperados está presente en este mensaje.")

    if contador[0] >= MAX_MENSAJES:
        print(f"\n✅ Capturados {MAX_MENSAJES} mensajes. Deteniendo...")
        pyRofex.close_websocket_connection()


def main():
    print("🚀 Iniciando sesión...")
    if not inicializar_sesion():
        return

    print(f"\n📡 Suscribiendo a: {TICKERS_TEST}")
    print(f"   (se mostrarán los primeros {MAX_MENSAJES} mensajes recibidos)\n")

    pyRofex.add_websocket_market_data_handler(handler_debug)

    pyRofex.market_data_subscription(
        tickers=TICKERS_TEST,
        entries=[
            pyRofex.MarketDataEntry.BIDS,
            pyRofex.MarketDataEntry.OFFERS,
            pyRofex.MarketDataEntry.LAST,
            pyRofex.MarketDataEntry.OPENING_PRICE,   # <── el que nos interesa
            pyRofex.MarketDataEntry.HIGH_PRICE,
            pyRofex.MarketDataEntry.LOW_PRICE,
            pyRofex.MarketDataEntry.TRADE_EFFECTIVE_VOLUME,
            pyRofex.MarketDataEntry.NOMINAL_VOLUME,
        ],
        depth=1
    )

    pyRofex.init_websocket_connection()

    # Mantenemos vivo el script hasta que se capturen MAX_MENSAJES
    timeout = 60  # segundos máximos de espera
    inicio = time.time()
    while contador[0] < MAX_MENSAJES and (time.time() - inicio) < timeout:
        time.sleep(0.5)

    if contador[0] < MAX_MENSAJES:
        print(f"\n⏰ Timeout de {timeout}s alcanzado con solo {contador[0]} mensajes recibidos.")
        pyRofex.close_websocket_connection()

    print("\n🏁 Test finalizado.")


if __name__ == "__main__":
    main()
