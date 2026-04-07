"""
Motor de Opciones GGAL - Servicio Headless
Idéntico a main_options.py pero sin UI (Textual/Rich).
Arquitectura event-driven: cada tick del WebSocket escribe el snapshot
en MongoDB de forma inmediata (throttle 300ms/símbolo).
"""
import threading
import time
import signal
import logging
import pyRofex
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from collections import defaultdict
from pymongo import UpdateOne

from Opciones.calculos_cuantitativos import (
    calc_intrinseco, find_iv, bs_delta, bs_gamma, bs_vega, bs_theta
)
from mongo_manager import MongoManager, get_mongo_client
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

        self.mongo      = MongoManager(db_name="Opciones", collection_name="Data")
        self._meta_col  = get_mongo_client()["Opciones"]["Metadata"]

        # Tasa: lee de Metadata si existe, si no usa el default y lo persiste
        cfg = self._meta_col.find_one({"type": "config"})
        self.tasa = cfg.get("tasa", 0.242) if cfg else 0.242
        self._meta_col.update_one(
            {"type": "config"},
            {"$set": {"tasa": self.tasa}},
            upsert=True
        )
        logger.info(f"Tasa libre de riesgo: {self.tasa:.3f}")

        self.mapa_opciones, self.agrupacion_strikes = self._generar_maestra()
        self.market_state = {}
        self.last_trade_cache = {}
        self._cache_lock = threading.Lock()

        self._inicializar_estado_memoria()

        # Pool acotado para guardar trades históricos (evita explosión de threads)
        self._trade_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="opt_trade")

        # Hilo único de escritura de snapshots a MongoDB (bulk_write cada 1s)
        threading.Thread(target=self._batch_snapshot_loop, daemon=True).start()

    def _generar_maestra(self):
        """Descarga el padrón y filtra opciones de GGAL para el próximo vencimiento."""
        res = pyRofex.get_detailed_instruments()
        mapa, agrupacion = {}, defaultdict(dict)
        if not res or res.get('status') != 'OK':
            return mapa, agrupacion

        hoy = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        # Primera pasada: encontrar el próximo vencimiento disponible
        expiries = set()
        for inst in res['instruments']:
            if inst.get('underlying') == "Grupo Financiero Galicia Merval":
                cfi = inst.get('cficode', '')
                vence_raw = inst.get('maturity_date', inst.get('maturityDate', ''))
                if cfi.startswith('O') and len(vence_raw) == 8:
                    try:
                        if datetime.strptime(vence_raw, "%Y%m%d") >= hoy:
                            expiries.add(vence_raw)
                    except ValueError:
                        pass

        if not expiries:
            return mapa, agrupacion

        proxima = min(expiries)  # YYYYMMDD → orden lexicográfico = orden cronológico
        logger.info(f"Vencimiento detectado: {proxima}")

        # Segunda pasada: cargar solo ese vencimiento
        for inst in res['instruments']:
            if inst.get('underlying') == "Grupo Financiero Galicia Merval":
                cfi = inst.get('cficode', '')
                vence_raw = inst.get('maturity_date', inst.get('maturityDate', ''))
                if vence_raw == proxima:
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
                'open': 0, 'high': 0, 'low': 0, 'ev': 0, 'closing_price': 0
            }

    def get_tickers_suscripcion(self):
        return list(self.market_state.keys())

    def update_price(self, ticker, data):
        """Maneja la entrada del WebSocket y actualiza la RAM.
        Los snapshots a MongoDB los maneja _batch_snapshot_loop en segundo plano.
        """
        if ticker not in self.market_state:
            return
        state = self.market_state[ticker]

        # 1. Puntas (BID/OFFER)
        if 'BI' in data and data['BI']:
            state['bid'] = data['BI'][0]['price']
        if 'OF' in data and data['OF']:
            state['offer'] = data['OF'][0]['price']

        # 2. Datos de Mercado (OP, HI, LO, EV)
        state['open']          = data.get('OP', data.get('OPENING_PRICE', state['open']))
        state['high']          = data.get('HI', data.get('HIGH_PRICE', state['high']))
        state['low']           = data.get('LO', data.get('LOW_PRICE', state['low']))
        state['ev']            = data.get('EV', data.get('TRADE_EFFECTIVE_VOLUME', state['ev']))
        state['closing_price'] = data.get('CL', data.get('CLOSING_PRICE', state['closing_price']))

        # 3. Último operado (LAST) — guarda trade histórico solo en ticks nuevos
        la = data.get('LA')
        if la and la.get('price', 0) > 0:
            state['last'] = la['price']
            ts = datetime.fromtimestamp(la['date'] / 1000.0)
            state['last_timestamp'] = ts

            if ticker in self.mapa_opciones:
                spawn = False
                with self._cache_lock:
                    if self.last_trade_cache.get(ticker) != ts:
                        self.last_trade_cache[ticker] = ts
                        spawn = True
                if spawn:
                    self._trade_pool.submit(self._guardar_en_mongo, ticker, state.copy(), ts)

    def _batch_snapshot_loop(self):
        """
        Hilo único de escritura a MongoDB.
        Cada 1s calcula los Greeks de todas las opciones activas y hace
        un único bulk_write a OptionsSnapshot — un solo round-trip a Atlas
        sin importar cuántos activos haya.
        """
        col = get_mongo_client()["Opciones"]["OptionsSnapshot"]
        _tick = 0

        while True:
            time.sleep(5)
            _tick += 1

            # Cada 60s lee la tasa de Metadata para que Streamlit pueda cambiarla
            if _tick % 60 == 0:
                try:
                    cfg = self._meta_col.find_one({"type": "config"})
                    if cfg and cfg.get("tasa"):
                        nueva = cfg["tasa"]
                        if abs(nueva - self.tasa) > 1e-6:
                            logger.info(f"Tasa actualizada: {self.tasa:.3f} → {nueva:.3f}")
                            self.tasa = nueva
                except Exception:
                    pass

            try:
                S = self.market_state.get(self.spot_symbol, {}).get('last', 0)
                ts = datetime.now()
                ops = []

                for sym, info in self.mapa_opciones.items():
                    md = self.market_state.get(sym, {})
                    bid   = md.get('bid', 0)
                    offer = md.get('offer', 0)
                    last  = md.get('last', 0)

                    # Saltear opciones sin ningún precio
                    if bid == 0 and offer == 0 and last == 0:
                        continue

                    K    = info['strike']
                    tipo = info['tipo']
                    T    = max((datetime.strptime(info['vence'], "%Y%m%d") - ts).days, 1) / 365.0
                    p_mid = (bid + offer) / 2 if bid > 0 and offer > 0 else last

                    doc = {
                        "updated_at": ts,
                        "symbol": sym,
                        "bid": bid, "offer": offer, "last": last,
                        "open":          md.get('open', 0),
                        "high":          md.get('high', 0),
                        "low":           md.get('low', 0),
                        "ev":            md.get('ev', 0),
                        "closing_price": md.get('closing_price', 0),
                        "strike": K, "tipo": tipo, "spot": S, "vence": info['vence'],
                    }

                    if S > 0 and p_mid > 0:
                        try:
                            vi   = calc_intrinseco(S, K, tipo)
                            p_iv = p_mid if p_mid > vi else vi + 0.1
                            iv   = find_iv(p_iv, S, K, T, self.tasa, tipo)
                            if iv > 0:
                                doc.update({
                                    "iv":    round(iv, 4),
                                    "delta": round(bs_delta(S, K, T, self.tasa, iv, tipo), 3),
                                    "gamma": round(bs_gamma(S, K, T, self.tasa, iv), 4),
                                    "vega":  round(bs_vega(S, K, T, self.tasa, iv), 2),
                                    "theta": round(bs_theta(S, K, T, self.tasa, iv, tipo), 2),
                                })
                        except Exception:
                            pass

                    ops.append(UpdateOne({"symbol": sym}, {"$set": doc}, upsert=True))

                if ops:
                    col.bulk_write(ops, ordered=False)

            except Exception as e:
                logger.error(f"Error en batch_snapshot_loop: {e}")

    def _guardar_en_mongo(self, ticker, state_copy, ts):
        """Calcula Griegas y persiste el trade histórico en Data."""
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
    logger.info(
        f"Motor corriendo (event-driven, snapshot cada 1s). "
        f"Suscripto a {len(engine.get_tickers_suscripcion())} activos."
    )

    while _running:
        time.sleep(1)

    ws_manager.cerrar_ws()
    logger.info("Motor apagado.")


if __name__ == "__main__":
    run()
