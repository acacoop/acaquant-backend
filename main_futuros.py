import pyRofex
import time
from datetime import datetime
from mongo_manager import MongoManager
from session_manager import inicializar_sesion

# 1. CONFIGURACIÓN LOCAL
TICKERS_RATIO = ["ORO/MAR26", "WTI/MAR26"]

# Estado global: Aquí persistimos la "foto"
market_snapshot = {
    "ORO/MAR26": {
        'bid': 0, 'bid_size': 0, 'offer': 0, 'offer_size': 0,
        'last': 0, 'last_size': 0, 'last_ts': None
    },
    "WTI/MAR26": {
        'bid': 0, 'bid_size': 0, 'offer': 0, 'offer_size': 0,
        'last': 0, 'last_size': 0, 'last_ts': None
    }
}
mongo = None


def message_handler(message):
    global mongo
    if message.get("type") == "Md":
        symbol = message["instrumentId"]["symbol"]
        md = message["marketData"]

        # Timestamp del REGISTRO (Cuándo sacamos la foto del mercado)
        # Usamos el del mensaje para ser precisos con el movimiento de puntas
        ts_msg = md.get('timestamp')
        registro_dt = datetime.fromtimestamp(ts_msg / 1000.0) if ts_msg else datetime.now()

        bids = md.get('BI') or []
        offs = md.get('OF') or []
        last_event = md.get('LA') or {}

        # 1. Actualizamos PUNTAS (Bid/Ask)
        if bids:
            market_snapshot[symbol]['bid'] = bids[0].get('price', 0)
            market_snapshot[symbol]['bid_size'] = bids[0].get('size', 0)
        if offs:
            market_snapshot[symbol]['offer'] = offs[0].get('price', 0)
            market_snapshot[symbol]['offer_size'] = offs[0].get('size', 0)

        # 2. Actualizamos LAST usando el 'date' interno de LA
        # Rofex siempre manda LA, así que leemos su fecha real.
        if last_event and last_event.get('price', 0) > 0:
            market_snapshot[symbol]['last'] = last_event.get('price', 0)
            market_snapshot[symbol]['last_size'] = last_event.get('size', 0)

            # --- EL FIX DE ORO ---
            # Leemos el campo 'date' que viene DENTRO de LA. Ese es el timestamp real del trade.
            trade_ts_ms = last_event.get('date')
            if trade_ts_ms:
                real_trade_dt = datetime.fromtimestamp(trade_ts_ms / 1000.0)
                market_snapshot[symbol]['last_ts'] = real_trade_dt
            # Si por alguna razón no viene 'date', mantenemos el anterior (no tocamos nada)

        # 3. GUARDADO DEL SNAPSHOT DUAL
        # Solo guardamos si tenemos datos vivos de ambos
        if market_snapshot["ORO/MAR26"]['bid'] > 0 and market_snapshot["WTI/MAR26"]['bid'] > 0:
            if mongo:
                oro = market_snapshot["ORO/MAR26"]
                wti = market_snapshot["WTI/MAR26"]

                registro_par = {
                    "timestamp_registro": registro_dt,  # Hora de la "foto" (cambio de punta)

                    "oro_bid": oro['bid'],
                    "oro_bid_size": oro['bid_size'],
                    "oro_offer": oro['offer'],
                    "oro_offer_size": oro['offer_size'],
                    "oro_last": oro['last'],
                    "oro_last_size": oro['last_size'],
                    "oro_last_ts": oro['last_ts'],  # Hora REAL de ejecución (13:37:32 fijo hasta nuevo trade)

                    "wti_bid": wti['bid'],
                    "wti_bid_size": wti['bid_size'],
                    "wti_offer": wti['offer'],
                    "wti_offer_size": wti['offer_size'],
                    "wti_last": wti['last'],
                    "wti_last_size": wti['last_size'],
                    "wti_last_ts": wti['last_ts']  # Hora REAL de ejecución WTI
                }

                try:
                    mongo.collection.insert_one(registro_par)
                except Exception as e:
                    print(f"Error Mongo: {e}")


def run():
    global mongo
    print("🚀 Iniciando Capturador Ratios (Fixed timestamps)...")

    if not inicializar_sesion():
        print("❌ Error de sesión")
        return

    mongo = MongoManager(db_name="Opciones", collection_name="ratio")

    pyRofex.init_websocket_connection()
    pyRofex.add_websocket_market_data_handler(message_handler)

    print(f"📡 Suscribiendo a: {TICKERS_RATIO}")
    pyRofex.market_data_subscription(
        tickers=TICKERS_RATIO,
        entries=[
            pyRofex.MarketDataEntry.BIDS,
            pyRofex.MarketDataEntry.OFFERS,
            pyRofex.MarketDataEntry.LAST
        ]
    )

    while True:
        try:
            if datetime.now().time() >= datetime.strptime("17:00:00", "%H:%M:%S").time():
                print("\n🌙 Mercado cerrado.")
                break
            # Mostramos en consola los timestamps para que veas que no mienten
            oro_ts = market_snapshot["ORO/MAR26"]['last_ts']
            wti_ts = market_snapshot["WTI/MAR26"]['last_ts']

            ts_str_oro = oro_ts.strftime('%H:%M:%S') if oro_ts else "--:--:--"
            ts_str_wti = wti_ts.strftime('%H:%M:%S') if wti_ts else "--:--:--"

            print(f"[{time.strftime('%H:%M:%S')}] Snapshot OK | Last ORO: {ts_str_oro} | Last WTI: {ts_str_wti}",
                  end='\r')
            time.sleep(1)

        except KeyboardInterrupt:
            break

    pyRofex.close_websocket_connection()


if __name__ == "__main__":
    run()