"""
Motor de Opciones GGAL - Servicio Headless
Idéntico a main_options.py pero sin UI (Textual/Rich).
Arquitectura event-driven: cada tick del WebSocket escribe el snapshot
en MongoDB de forma inmediata (throttle 300ms/símbolo).
"""
import logging
import signal
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pyRofex
from pymongo import UpdateOne

from core.mongo import get_mongo_client
from core.rofex_session import inicializar_sesion
from core.websocket import WebSocketManager
from quant.black_scholes import bs_delta, bs_gamma, bs_theta, bs_vega, calc_intrinseco, find_iv

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
# Persistencia específica de opciones
# ==========================================
# Antes vivía en core/mongo.py, pero su única responsabilidad es escribir a
# colecciones `Opciones.*`. Es lógica de dominio, no de infraestructura
# compartida → se quedó acá, junto al único consumidor.
class MongoManager:
    def __init__(self, db_name="Opciones", collection_name="Data"):
        try:
            self.client = get_mongo_client()
            self.db = self.client[db_name]
            self.collection = self.db[collection_name]
            self.client.server_info()
            logger.info(f"MongoDB Conectado -> DB: {db_name} | Coll: {collection_name}")
        except Exception as e:
            logger.error(f"Error conexion MongoDB: {e}")

    def guardar_operacion_unica(self, symbol, data, server_time=None, griegas=None):
        """Guarda una operacion individual en Opciones.Data."""
        try:
            registro = {
                "timestamp": server_time if server_time else datetime.now(),
                "symbol": symbol,
                "bid": data.get('bid', 0),
                "bid_size": data.get('bid_size', 0),
                "offer": data.get('offer', 0),
                "offer_size": data.get('offer_size', 0),
                "last": data.get('last', 0),
                "last_size": data.get('last_size', 0),
                "last_timestamp": data.get('last_timestamp'),
                "strike": data.get('strike'),
                "tipo": data.get('tipo'),
                "spot": data.get('spot'),
                "open": data.get('open', 0),
                "high": data.get('high', 0),
                "low": data.get('low', 0),
                "ev": data.get('ev', 0),
            }
            if griegas and isinstance(griegas, dict):
                registro.update(griegas)
            self.collection.insert_one(registro)
        except Exception as e:
            logger.error(f"Error al guardar operacion: {e}")

    def guardar_snapshot_opciones(self, symbol, data, griegas=None):
        """Upsert del estado más reciente de cada opción en Opciones.OptionsSnapshot."""
        try:
            doc = {
                "updated_at": datetime.now(),
                "symbol": symbol,
                "bid":    data.get('bid', 0),
                "offer":  data.get('offer', 0),
                "last":   data.get('last', 0),
                "open":   data.get('open', 0),
                "high":   data.get('high', 0),
                "low":    data.get('low', 0),
                "ev":     data.get('ev', 0),
                "strike": data.get('strike'),
                "tipo":   data.get('tipo'),
                "spot":   data.get('spot', 0),
            }
            if griegas and isinstance(griegas, dict):
                doc.update(griegas)
            self.db["OptionsSnapshot"].update_one(
                {"symbol": symbol},
                {"$set": doc},
                upsert=True
            )
        except Exception as e:
            logger.error(f"Error al guardar snapshot: {e}")


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
        self._purgar_snapshots_fuera_de_mapa()

        # Pool acotado para guardar trades históricos (evita explosión de threads)
        self._trade_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="opt_trade")

        # Hilo único de escritura de snapshots a MongoDB (bulk_write cada 1s)
        threading.Thread(target=self._batch_snapshot_loop, daemon=True).start()

    def _generar_maestra(self):
        """Descarga el padrón y filtra opciones GGAL según la config del Manager.

        Si `Opciones.Metadata.expiries` tiene vencimientos (lista YYYYMMDD),
        trackea SOLO esos. Si está vacía/None → auto-pick (próximo > hoy).
        """
        res = pyRofex.get_detailed_instruments()
        mapa, agrupacion = {}, defaultdict(dict)
        if not res or res.get('status') != 'OK':
            return mapa, agrupacion

        hoy = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        # Primera pasada: todas las expiries futuras disponibles en ROFEX hoy.
        # Usamos `> hoy` para saltar el OPEX del propio día.
        expiries_futuras = set()
        for inst in res['instruments']:
            if inst.get('underlying') == "Grupo Financiero Galicia Merval":
                cfi = inst.get('cficode', '')
                vence_raw = inst.get('maturity_date', inst.get('maturityDate', ''))
                if cfi.startswith('O') and len(vence_raw) == 8:
                    try:
                        if datetime.strptime(vence_raw, "%Y%m%d") > hoy:
                            expiries_futuras.add(vence_raw)
                    except ValueError:
                        pass

        if not expiries_futuras:
            return mapa, agrupacion

        # Publicar disponibles (lo usa el Manager para el multi-select)
        try:
            self._meta_col.update_one(
                {"type": "config"},
                {"$set": {
                    "expiries_disponibles": sorted(expiries_futuras),
                    "expiries_updated_at":  datetime.utcnow(),
                }},
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"No se pudo publicar expiries_disponibles: {e}")

        # Leer config del Manager (qué vencimientos eligió el user)
        cfg = self._meta_col.find_one({"type": "config"}) or {}
        expiries_cfg = [e for e in (cfg.get("expiries") or []) if isinstance(e, str)]

        if expiries_cfg:
            expiries_usar = set(expiries_cfg) & expiries_futuras
            ignoradas = set(expiries_cfg) - expiries_futuras
            if ignoradas:
                logger.warning(f"Expiries configuradas ya vencidas/ausentes: {sorted(ignoradas)}")
            if not expiries_usar:
                logger.warning("Ninguna expiry configurada está disponible → cayendo a auto-pick")
                expiries_usar = {min(expiries_futuras)}
        else:
            expiries_usar = {min(expiries_futuras)}

        logger.info(f"Vencimientos a trackear: {sorted(expiries_usar)}")

        # Segunda pasada: cargar strikes de los vencimientos elegidos
        for inst in res['instruments']:
            if inst.get('underlying') != "Grupo Financiero Galicia Merval":
                continue
            cfi = inst.get('cficode', '')
            vence_raw = inst.get('maturity_date', inst.get('maturityDate', ''))
            if vence_raw in expiries_usar:
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

    def _purgar_snapshots_fuera_de_mapa(self):
        """Borra docs de OptionsSnapshot cuyo symbol no esté en el mapa vigente.

        Mantiene la colección alineada con la serie de opciones que se está
        trackeando activamente. Sin esto, los snapshots de ruedas pasadas
        quedaban para siempre y contaminaban la vista del frontend.
        """
        if not self.mapa_opciones:
            return
        try:
            col = get_mongo_client()["Opciones"]["OptionsSnapshot"]
            resultado = col.delete_many({"symbol": {"$nin": list(self.mapa_opciones.keys())}})
            if resultado.deleted_count:
                logger.info(f"🧹 OptionsSnapshot: purgados {resultado.deleted_count} docs de series anteriores.")
        except Exception as e:
            logger.error(f"Error purgando OptionsSnapshot: {e}")

    def refrescar_mapa(self) -> list[str]:
        """Recomputa mapa_opciones desde pyRofex y devuelve símbolos nuevos.

        Llamar periódicamente (ej. cada 30 min) para pillar contratos que
        ROFEX publica durante la rueda sin reiniciar el motor. Si cambió
        el vencimiento objetivo (rotación post-OPEX), reemplaza el mapa
        entero — los símbolos viejos quedan suscriptos en el WS pero el
        loop los ignora porque no están en mapa_opciones.
        """
        nuevo_mapa, nueva_agrup = self._generar_maestra()
        if not nuevo_mapa:
            return []

        viejo_set = set(self.mapa_opciones.keys())
        nuevo_set = set(nuevo_mapa.keys())
        if nuevo_set == viejo_set:
            return []

        vencs_viejos = {v['vence'] for v in self.mapa_opciones.values()} if self.mapa_opciones else set()
        vencs_nuevos = {v['vence'] for v in nuevo_mapa.values()}
        if vencs_viejos and vencs_nuevos and vencs_viejos != vencs_nuevos:
            logger.info(f"🔄 Rotación de vencimiento: {sorted(vencs_viejos)} → {sorted(vencs_nuevos)}")

        self.mapa_opciones = nuevo_mapa
        self.agrupacion_strikes = nueva_agrup

        nuevos = sorted(nuevo_set - viejo_set)
        for sym in nuevos:
            if sym not in self.market_state:
                self.market_state[sym] = {
                    'bid': 0, 'offer': 0, 'last': 0, 'last_timestamp': None,
                    'open': 0, 'high': 0, 'low': 0, 'ev': 0, 'closing_price': 0
                }

        # Si hubo rotación de vencimiento, limpiar docs de la serie anterior.
        self._purgar_snapshots_fuera_de_mapa()
        return nuevos

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
        if data.get('BI'):
            state['bid'] = data['BI'][0]['price']
        if data.get('OF'):
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
        # Cache del último estado que vimos por símbolo — evita upserts
        # idénticos. Suele reducir ~90% de writes entre ticks quietos.
        _last_seen: dict[str, tuple] = {}
        _SPOT_RESYNC_EVERY = 12  # reescribir igual cada 12 ticks (~1 min) para refrescar `updated_at`

        while True:
            time.sleep(5)
            _tick += 1

            # Cada ~5 min lee Metadata para aplicar cambios de tasa y expiries
            if _tick % 60 == 0:
                try:
                    cfg = self._meta_col.find_one({"type": "config"}) or {}
                    nueva = cfg.get("tasa")
                    if nueva and abs(nueva - self.tasa) > 1e-6:
                        logger.info(f"Tasa actualizada: {self.tasa:.3f} → {nueva:.3f}")
                        self.tasa = nueva

                    # Si el user cambió los expiries desde el Manager, refrescar
                    expiries_cfg     = set(cfg.get("expiries") or [])
                    expiries_actuales = {v['vence'] for v in self.mapa_opciones.values()}
                    if expiries_cfg and expiries_cfg != expiries_actuales:
                        logger.info("Config de expiries cambió → refrescando mapa")
                        nuevos = self.refrescar_mapa()
                        ws = getattr(self, "_ws_ref", None)
                        if nuevos and ws is not None:
                            ws.agregar_suscripciones(nuevos)
                    elif not expiries_cfg and len(expiries_actuales) > 1:
                        # User volvió a auto-pick (expiries=[]), pero teníamos más de uno → refrescar
                        logger.info("Auto-pick re-activado → refrescando mapa")
                        nuevos = self.refrescar_mapa()
                        ws = getattr(self, "_ws_ref", None)
                        if nuevos and ws is not None:
                            ws.agregar_suscripciones(nuevos)
                except Exception as e:
                    logger.warning(f"Error leyendo config de Metadata: {e}")

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

                    # Dirty check: si nada del book/stats/spot cambió desde el
                    # tick anterior, skip el upsert (salvo cada ~1 min para
                    # refrescar `updated_at` y que la UI sepa que sigue vivo).
                    huella = (bid, offer, last,
                              md.get('open', 0), md.get('high', 0), md.get('low', 0),
                              md.get('ev', 0), md.get('closing_price', 0), S)
                    prev = _last_seen.get(sym)
                    if prev == huella and (_tick % _SPOT_RESYNC_EVERY) != 0:
                        continue
                    _last_seen[sym] = huella

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
    engine._ws_ref = ws_manager  # permite que el engine agregue suscripciones dinámicas
    ws_manager.iniciar_ws(engine.get_tickers_suscripcion())
    logger.info(
        f"Motor corriendo (event-driven, snapshot cada 1s). "
        f"Suscripto a {len(engine.get_tickers_suscripcion())} activos."
    )

    # Refresh del padrón cada 30 min: capta strikes nuevos publicados por
    # ROFEX durante la rueda (y rota el vencimiento si hace falta).
    def _refresh_loop():
        while _running:
            for _ in range(1800):  # 30 min en ticks de 1s, cortable por _running
                if not _running:
                    return
                time.sleep(1)
            try:
                nuevos = engine.refrescar_mapa()
                if nuevos:
                    logger.info(f"➕ {len(nuevos)} símbolos nuevos detectados, suscribiendo...")
                    ws_manager.agregar_suscripciones(nuevos)
            except Exception as e:
                logger.error(f"Error en _refresh_loop: {e}")

    threading.Thread(target=_refresh_loop, daemon=True).start()

    while _running:
        time.sleep(1)

    ws_manager.cerrar_ws()
    logger.info("Motor apagado.")


if __name__ == "__main__":
    run()
