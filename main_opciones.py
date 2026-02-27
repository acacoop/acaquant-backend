import pyRofex
import time
from datetime import datetime
# Importamos tus funciones de calculo
from calculos_cuantitativos import (
    calc_intrinseco, find_iv, bs_delta, bs_gamma, bs_vega, bs_theta
)
from config_GGAL_OPCIONES import TICKERS_DINAMICOS, MAPA_STRIKES, VENCIMIENTOS_INFO
from excel_opciones_manager import ExcelOpcionesManager
from mongo_manager import MongoManager
from session_manager import inicializar_sesion

market_data = {}
# Cacheamos por TIMESTAMP del trade, que es el ID más seguro
last_trade_cache = {}


def message_handler(message):
    if message.get("type") == "Md":
        symbol = message["instrumentId"]["symbol"]
        md = message["marketData"]

        # 1. Obtenemos datos crudos
        bids = md.get('BI') or [{'price': 0, 'size': 0}]
        offs = md.get('OF') or [{'price': 0, 'size': 0}]
        last_event = md.get('LA') or {'price': 0, 'size': 0}

        # 2. Inicializamos si no existe
        if symbol not in market_data:
            market_data[symbol] = {
                'bid': 0, 'bid_size': 0, 'offer': 0, 'offer_size': 0,
                'last': 0, 'last_size': 0, 'timestamp': None, 'last_timestamp': None
            }

        # 3. Actualizamos estado actual
        market_data[symbol]['bid'] = bids[0]['price']
        market_data[symbol]['bid_size'] = bids[0]['size']
        market_data[symbol]['offer'] = offs[0]['price']
        market_data[symbol]['offer_size'] = offs[0]['size']
        market_data[symbol]['timestamp'] = md.get('timestamp')  # Hora del mensaje

        # 4. LÓGICA DE TIMESTAMP REAL (Igual que en Futuros)
        # Si hay precio en LA, extraemos su fecha real ('date')
        if last_event.get('price', 0) > 0:
            market_data[symbol]['last'] = last_event['price']
            market_data[symbol]['last_size'] = last_event['size']

            # EL DATO CLAVE: La fecha que viene ADENTRO del objeto Last
            trade_ts_ms = last_event.get('date')
            if trade_ts_ms:
                real_trade_dt = datetime.fromtimestamp(trade_ts_ms / 1000.0)
                market_data[symbol]['last_timestamp'] = real_trade_dt


def run():
    if not inicializar_sesion(): return

    excel = ExcelOpcionesManager(TICKERS_DINAMICOS, MAPA_STRIKES)
    # Asegurate que tu MongoManager tenga el update para aceptar 'last_timestamp'
    mongo = MongoManager(db_name="Opciones", collection_name="Data")

    pyRofex.init_websocket_connection()
    pyRofex.add_websocket_market_data_handler(message_handler)

    print(f"📡 Suscribiendo a {len(TICKERS_DINAMICOS)} opciones...")
    pyRofex.market_data_subscription(tickers=TICKERS_DINAMICOS, entries=[
        pyRofex.MarketDataEntry.BIDS, pyRofex.MarketDataEntry.OFFERS, pyRofex.MarketDataEntry.LAST
    ])

    print("🚀 Monitor de Opciones ONLINE (Timestamps Reales).")

    while True:
        try:
            # Corte 17:00
            if datetime.now().time() >= datetime.strptime("17:00:00", "%H:%M:%S").time():
                print("\n🌙 Mercado cerrado.")
                break

            if market_data:
                # --- EXCEL (Vista) ---
                excel.escribir_data(market_data)

                # --- MONGO (Trades Reales + Griegas) ---
                spot_symbol = "MERV - XMEV - GGAL - 24hs"
                S = market_data.get(spot_symbol, {}).get('last', 0)
                r = 0.31

                for symbol in TICKERS_DINAMICOS:
                    data = market_data.get(symbol)
                    # Si no hay last o no hay timestamp real de trade, pasamos
                    if not data or data['last'] <= 0 or not data.get('last_timestamp'):
                        continue

                    # USAMOS EL TIMESTAMP REAL COMO ID ÚNICO
                    # Si la fecha del trade no cambió, es el mismo trade repetido por Rofex
                    current_trade_ts = data['last_timestamp']

                    if last_trade_cache.get(symbol) != current_trade_ts:

                        # --- CÁLCULO DE GRIEGAS (On Demand) ---
                        # Calculamos las griegas CON LA FOTO ACTUAL de Spot y Volatilidad
                        # para registrar "qué griegas tenía este trade en este momento".
                        griegas = None
                        info = MAPA_STRIKES.get(symbol, {})
                        tipo = info.get('tipo')

                        if tipo and tipo != 'ACCION' and S > 0:
                            K = info.get('strike', 0)
                            vto_key = info.get('vencimiento')
                            T = VENCIMIENTOS_INFO.get(vto_key, 0) / 365

                            p_ref = (data['bid'] + data['offer']) / 2 if data['bid'] > 0 and data['offer'] > 0 else \
                            data['last']
                            vi = calc_intrinseco(S, K, tipo)
                            p_iv = p_ref if p_ref > vi else vi + 0.1

                            iv = find_iv(p_iv, S, K, T, r, tipo)
                            if iv > 0:
                                griegas = {
                                    "iv": round(iv, 4),
                                    "delta": round(bs_delta(S, K, T, r, iv, tipo), 3),
                                    "gamma": round(bs_gamma(S, K, T, r, iv), 4),
                                    "vega": round(bs_vega(S, K, T, r, iv), 2),
                                    "theta": round(bs_theta(S, K, T, r, iv, tipo), 2)
                                }

                        # --- GUARDADO ---
                        # server_time ahora es el TIMESTAMP DE REGISTRO (Cuándo lo vimos)
                        # data['last_timestamp'] va adentro del data y es el TIMESTAMP DE EJECUCIÓN
                        now_dt = datetime.now()

                        mongo.guardar_operacion_unica(
                            symbol,
                            data,
                            server_time=now_dt,
                            griegas=griegas
                        )

                        # Actualizamos cache con el timestamp del trade
                        last_trade_cache[symbol] = current_trade_ts

            time.sleep(1.2)

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)

    pyRofex.close_websocket_connection()


if __name__ == "__main__":
    run()