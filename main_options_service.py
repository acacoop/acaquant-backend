"""
Motor de Opciones GGAL - Servicio Headless
Idéntico a main_options.py pero sin UI (Textual/Rich).
Corre como daemon: WebSocket → calcula griegas → guarda en MongoDB.
"""
import os
import threading
import time
import signal
import logging
import pyRofex
from datetime import datetime
from collections import defaultdict

from Opciones.calculos_cuantitativos import (
    calc_intrinseco, find_iv, bs_delta, bs_gamma, bs_vega, bs_theta
)
from mongo_manager import MongoManager
from session_manager import inicializar_sesion
from websocket_manager import WebSocketManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)
logger = logging.getLogger("MotorOpciones")

_running = True


def _handle_signal(sig, frame):
    global _running
    logger.info("Señal de cierre recibida, apagando...")
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ==========================================
# EL CEREBRO: OptionsEngine (idéntico a main_options.py)
# ==========================================
class OptionsEngine:
    def __init__(self):
        self.spot_symbol = "MERV - XMEV - GGAL - 24hs"
        self.tasa = 0.242

        self.mongo = MongoManager(db_name="Opciones", collection_name="Data")

        self.mapa_opciones, self.agrupacion_strikes = self._generar_maestra()
        self.market_state = {}
        self.last_trade_cache = {}

        self._inicializar_estado_memoria()

    def _generar_maestra(self):
        """Descarga el padrón y filtra opciones de GGAL para Abril."""
        res = pyRofex.get_detailed_instruments()
        mapa, agrupacion = {}, defaultdict(dict)
        if not res or res.get('status') != 'OK':
            return mapa, agrupacion

        for inst in res['instruments']:
            if inst.get('underlying') == "Grupo Financiero Galicia Merval":
                cfi = inst.get('cficode', '')
                vence_raw = inst.get('maturity_date', inst.get('maturityDate', ''))

                # Filtro para Abril (04)
                if cfi.startswith('O') and len(vence_raw) == 8 and vence_raw[4:6] == "04":
                    sym = inst['instrumentId']['symbol']
                    strike = float(inst.get('strike', 0))
                    tipo = 'CALL' if cfi == 'OCASPS' else 'PUT' if cfi == 'OPASPS' else None
                    if tipo and strike:
                        mapa[sym] = {'strike': strike, 'tipo': tipo, 'vence': vence_raw}
                        agrupacion[strike][tipo] = sym
        return mapa, dict(agrupacion)

    def _inicializar_estado_memoria(self):
        """Prepara el diccionario para el Spot, Opciones y Estrategias."""
        tickers_a_monitorear = list(self.mapa_opciones.keys()) + [self.spot_symbol]
        for t in set(tickers_a_monitorear):
            self.market_state[t] = {
                'bid': 0, 'offer': 0, 'last': 0, 'last_timestamp': None,
                'open': 0, 'high': 0, 'low': 0, 'ev': 0
            }

    def get_tickers_suscripcion(self):
        return list(self.market_state.keys())

    def update_price(self, ticker, data):
        """Maneja la entrada del WebSocket y actualiza la RAM."""
        if ticker not in self.market_state:
            return
        state = self.market_state[ticker]

        # 1. Puntas (BID/OFFER)
        if 'BI' in data and data['BI']:
            state['bid'] = data['BI'][0]['price']
        if 'OF' in data and data['OF']:
            state['offer'] = data['OF'][0]['price']

        # 2. Datos de Mercado (OP, HI, LO, EV) con blindaje de nombres
        state['open'] = data.get('OP', data.get('OPENING_PRICE', state['open']))
        state['high'] = data.get('HI', data.get('HIGH_PRICE', state['high']))
        state['low']  = data.get('LO', data.get('LOW_PRICE', state['low']))
        state['ev']   = data.get('EV', data.get('TRADE_EFFECTIVE_VOLUME', state['ev']))

        # 3. Último operado (LAST)
        la = data.get('LA')
        if la and la.get('price', 0) > 0:
            state['last'] = la['price']
            ts = datetime.fromtimestamp(la['date'] / 1000.0)
            state['last_timestamp'] = ts

            if ticker in self.mapa_opciones:
                if self.last_trade_cache.get(ticker) != ts:
                    self.last_trade_cache[ticker] = ts
                    threading.Thread(
                        target=self._guardar_en_mongo,
                        args=(ticker, state.copy(), ts),
                        daemon=True
                    ).start()

    def _guardar_en_mongo(self, ticker, state_copy, ts):
        """Calcula Griegas y persiste en segundo plano."""
        S = self.market_state[self.spot_symbol]['last']
        if S <= 0:
            return

        info = self.mapa_opciones[ticker]
        K = info['strike']
        T = max((datetime.strptime(info['vence'], "%Y%m%d") - datetime.now()).days, 1) / 365.0

        vi = calc_intrinseco(S, K, info['tipo'])
        p_iv = state_copy['last'] if state_copy['last'] > vi else vi + 0.1

        griegas = None
        try:
            iv = find_iv(p_iv, S, K, T, self.tasa, info['tipo'])
            if iv > 0:
                griegas = {
                    "iv":    round(iv, 4),
                    "delta": round(bs_delta(S, K, T, self.tasa, iv, info['tipo']), 3),
                    "gamma": round(bs_gamma(S, K, T, self.tasa, iv), 4),
                    "vega":  round(bs_vega(S, K, T, self.tasa, iv), 2),
                    "theta": round(bs_theta(S, K, T, self.tasa, iv, info['tipo']), 2)
                }
        except Exception:
            pass

        state_copy['strike'] = K
        state_copy['tipo']   = info['tipo']
        state_copy['spot']   = S
        self.mongo.guardar_operacion_unica(
            ticker, state_copy, server_time=datetime.now(), griegas=griegas
        )


# ==========================================
# ORQUESTADOR (sin UI)
# ==========================================
def run():
    global _running
    if not inicializar_sesion():
        return

    engine = OptionsEngine()
    if not engine.mapa_opciones:
        logger.error("No se encontraron opciones de GGAL en el mercado.")
        return

    ws_manager = WebSocketManager(engine)
    ws_manager.iniciar_ws(engine.get_tickers_suscripcion())
    logger.info(f"Motor corriendo. Suscripto a {len(engine.get_tickers_suscripcion())} activos.")

    while _running:
        time.sleep(1)

    ws_manager.cerrar_ws()
    logger.info("Motor apagado.")


if __name__ == "__main__":
    run()
