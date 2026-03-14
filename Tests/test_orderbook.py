import time
import json
import pyRofex
from datetime import datetime
from session_manager import inicializar_sesion

TICKER = "MERV - XMEV - X15Y6 - 24hs"
LOG_FILENAME = "../Diagnostico_X15Y6_RAW.log"

with open(LOG_FILENAME, "w", encoding="utf-8") as f:
    f.write(f"--- INICIO DE LOG RAW PARA {TICKER} ---\n")


def raw_handler(message):
    try:
        local_time = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        data = message.get("marketData", {})

        last = data.get("LA")
        nv = data.get("NV")
        has_bids = "BI" in data
        has_offers = "OF" in data

        # Armamos una etiqueta para saber QUÉ nos mandó el broker
        contenido = []
        if last: contenido.append("LAST")
        if nv is not None: contenido.append("NV")
        if has_bids: contenido.append("BIDS")
        if has_offers: contenido.append("OFFERS")

        tag = "+".join(contenido)

        px = last.get("price") if last else "N/A"
        sz = last.get("size") if last else "N/A"
        ts = last.get("date") if last else "N/A"

        # Mostramos qué trajo el paquete para entender por qué llegó
        print(f"[{local_time}] LLEGÓ [{tag}] -> LA_Px: {px} | LA_Sz: {sz} | Rofex_MS: {ts} | NV: {nv}")

        with open(LOG_FILENAME, mode='a', encoding='utf-8') as file:
            raw_str = json.dumps(message)
            file.write(f"[{local_time}] {raw_str}\n")

    except Exception as e:
        print(f"Error procesando mensaje: {e}")


def run_test():
    print("Iniciando Test 100% CRUDO (Con Etiquetas de Contenido)...")
    if not inicializar_sesion(): return

    pyRofex.add_websocket_market_data_handler(raw_handler)

    pyRofex.market_data_subscription(
        tickers=[TICKER],
        entries=[
            pyRofex.MarketDataEntry.LAST,
            pyRofex.MarketDataEntry.NOMINAL_VOLUME,
            pyRofex.MarketDataEntry.BIDS,
            pyRofex.MarketDataEntry.OFFERS
        ]
    )

    pyRofex.init_websocket_connection()
    print(f"✅ Escuchando TODO. Guardando los paquetes íntegros en: {LOG_FILENAME}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pyRofex.close_websocket_connection()


if __name__ == "__main__":
    run_test()