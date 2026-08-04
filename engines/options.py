"""
Motor de Opciones GGAL - Servicio Headless
Idéntico a main_options.py pero sin UI (Textual/Rich).
Arquitectura event-driven: el WS actualiza la RAM y dos hilos persisten a
Postgres — el grid con Greeks (mercado.options_snapshot, upsert cada 5s) y los
trades (mercado.options_data, en lotes de 1s).
"""
import logging
import signal
import threading
import time
from collections import defaultdict
from datetime import datetime

import pyRofex

from core.rofex_session import inicializar_sesion
from core.threads import lanzar_hilo_vital
from core.websocket import WebSocketManager
from quant.black_scholes import bs_greeks, calc_intrinseco, find_iv

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
# Persistencia específica de opciones (SQL-native)
# ==========================================
# Escribe los ticks de la chain a mercado.options_data (Postgres). Antes escribía
# Mongo Opciones.Data; tras el cutover de opciones la fuente es SQL.
class OptionsDataWriter:
    def guardar_operaciones(self, trades):
        """Persiste un LOTE de ticks SQL-NATIVE en mercado.options_data (append).

        `trades` = [{symbol, data, server_time, griegas}]. Un solo append_native por
        lote (un checkout de conexión + un executemany) en vez de un INSERT por trade.
        Solo vencimiento vigente (el symbol está en el mapa); la purga de series viejas
        la hacen _purgar_snapshots_fuera_de_mapa + jobs/archive_options_data. `ts` naive
        (datetime.now() ART) → timestamp SIN tz.
        """
        if not trades:
            return
        try:
            from core import pg_mirror
            rows = []
            for t in trades:
                symbol = t["symbol"]
                data   = t["data"]
                registro = {
                    "timestamp": t.get("server_time") or datetime.now(),
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
                griegas = t.get("griegas")
                if griegas and isinstance(griegas, dict):
                    registro.update(griegas)
                rows.append({
                    "symbol": symbol,
                    "ts": registro["timestamp"],
                    "data": pg_mirror.doc_iso(registro),
                })
            pg_mirror.append_native("options_data", rows)
        except Exception as e:
            logger.error(f"Error al guardar operaciones: {e}")


# ==========================================
# EL CEREBRO: OptionsEngine (idéntico a main_options.py)
# ==========================================
# Las opciones GGAL se identifican por el ROOT del símbolo (GFGC=call, GFGV=put) + el
# cfi de opción equity. ROFEX cambió el `underlying` de 'Grupo Financiero Galicia Merval'
# a la razón social completa ('GRUPO FINANCIERO GALICIA S.A ESCRIT.  B  1 V') el 2026-06-16
# → filtrar por underlying mató el motor (matcheaba 0). El símbolo GFG* es estable.
_OPT_CFIS = ("OCASPS", "OPASPS")  # OCASPS = CALL, OPASPS = PUT


def _es_opcion_ggal(inst: dict) -> bool:
    if inst.get("cficode") not in _OPT_CFIS:
        return False
    sym = (inst.get("instrumentId") or {}).get("symbol") or ""
    return "GFG" in sym


class OptionsEngine:

    def __init__(self):
        self.spot_symbol = "MERV - XMEV - GGAL - 24hs"

        self.writer     = OptionsDataWriter()

        # Tasa: lee la config SQL-native (mercado.options_metadata.config). Si no existe
        # usa el default y lo persiste. La config es multi-writer (motor + Manager +
        # update_opciones_tasa) → se mergea por campo con merge_jsonb_native (`$set` parcial).
        cfg = self._read_config()
        self.tasa = cfg.get("tasa", 0.242)
        self._merge_config({"tasa": self.tasa})
        logger.info(f"Tasa libre de riesgo: {self.tasa:.3f}")

        self.mapa_opciones, self.agrupacion_strikes = self._generar_maestra()
        self.market_state = {}
        self.last_trade_cache = {}
        self._cache_lock = threading.Lock()

        # Buffer de trades + flush cada 1s (mismo patrón que engines/valores.py):
        # el WS solo encola, y un hilo dedicado calcula Greeks y hace UN insert por
        # lote. Antes era un INSERT (checkout de conexión + commit) por CADA trade.
        self.trade_buffer = []
        self._buffer_lock = threading.Lock()

        self._inicializar_estado_memoria()
        self._purgar_snapshots_fuera_de_mapa()

        # Hilo único de escritura del grid de opciones a Postgres (upsert cada 5s)
        lanzar_hilo_vital(self._batch_snapshot_loop, "batch_snapshot_loop")
        # Hilo único de escritura de los trades a Postgres (lotes de 1s)
        lanzar_hilo_vital(self._flush_trades_loop, "flush_trades_loop")

    @staticmethod
    def _read_config() -> dict:
        """Config del motor desde SQL (mercado.options_metadata.config): tasa + expiries."""
        from core import pg_mirror
        return pg_mirror.read_native_doc("options_metadata", ["type"], ["config"])

    @staticmethod
    def _merge_config(patch: dict) -> None:
        """Mergea campos en options_metadata.config sin pisar los que escriben el Manager
        (expiries) ni update_opciones_tasa (tasa) — `$set` parcial atómico vía `||` jsonb."""
        from core import pg_mirror
        pg_mirror.merge_jsonb_native("options_metadata", ["type"], ["config"], patch)

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
            if not _es_opcion_ggal(inst):
                continue
            vence_raw = inst.get('maturity_date', inst.get('maturityDate', ''))
            if len(vence_raw) == 8:
                try:
                    if datetime.strptime(vence_raw, "%Y%m%d") > hoy:
                        expiries_futuras.add(vence_raw)
                except ValueError:
                    pass

        if not expiries_futuras:
            return mapa, agrupacion

        # Publicar disponibles (lo usa el Manager para el multi-select) — SQL-native,
        # mergeado sobre config para no pisar `expiries`/`tasa`.
        try:
            self._merge_config({
                "expiries_disponibles": sorted(expiries_futuras),
                "expiries_updated_at":  datetime.utcnow(),
            })
        except Exception as e:
            logger.warning(f"No se pudo publicar expiries_disponibles: {e}")

        # Leer config del Manager (qué vencimientos eligió el user) desde SQL
        cfg = self._read_config()
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
            if not _es_opcion_ggal(inst):
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
        """Borra (SQL) las filas cuyo symbol no esté en el mapa vigente.

        Mantiene options_snapshot (grid) Y options_data (ticks intradía) acotados a
        la serie de opciones que se está trackeando activamente — sin esto, los
        strikes/vencimientos de ruedas pasadas quedaban para siempre y contaminaban
        la vista del frontend. SQL-native: ya NO purga Mongo. archive_options_data
        hace la purga diaria adicional por timestamp (ts < hoy ART).
        """
        if not self.mapa_opciones:
            return
        vigentes = list(self.mapa_opciones.keys())
        try:
            from core.postgres import get_pool
            with get_pool().connection() as _cn, _cn.cursor() as _cur:
                _cur.execute("DELETE FROM options_snapshot WHERE symbol <> ALL(%s)",
                             (vigentes,))
                _cur.execute("DELETE FROM options_data WHERE symbol <> ALL(%s)",
                             (vigentes,))
        except Exception as e:
            logger.error(f"Error purgando options_snapshot/options_data SQL: {e}")

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
        La persistencia a Postgres la hacen en segundo plano _batch_snapshot_loop
        (grid) y _flush_trades_loop (trades).
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
                nuevo = False
                with self._cache_lock:
                    if self.last_trade_cache.get(ticker) != ts:
                        self.last_trade_cache[ticker] = ts
                        nuevo = True
                if nuevo:
                    # `server_time` se sella ACÁ (no en el flush) para que la columna
                    # `ts` de options_data siga siendo el instante en que vimos el
                    # trade, igual que antes — el batch solo difiere la escritura.
                    with self._buffer_lock:
                        self.trade_buffer.append((ticker, state.copy(), datetime.now()))

    def _batch_snapshot_loop(self):
        """
        Hilo único de escritura del grid de opciones, SQL-NATIVE.
        Cada 5s calcula los Greeks de todas las opciones activas y hace un único
        write_native (UPSERT por symbol) a mercado.options_snapshot — un solo
        round-trip a Postgres sin importar cuántos activos haya. Ya NO escribe
        Mongo Opciones.OptionsSnapshot (la fuente es SQL).
        """
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
                    cfg = self._read_config()
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
                docs = []

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
                                g = bs_greeks(S, K, T, self.tasa, iv, tipo)
                                doc.update({
                                    "iv":    round(iv, 4),
                                    "delta": round(g["delta"], 3),
                                    "gamma": round(g["gamma"], 4),
                                    "vega":  round(g["vega"], 2),
                                    "theta": round(g["theta"], 2),
                                })
                        except Exception:
                            pass

                    docs.append(doc)

                if docs:
                    # SQL-native: chain live de opciones (grid) → mercado.options_snapshot
                    # (UPSERT por symbol). Solo vencimientos vigentes — la purga de series
                    # viejas la maneja _purgar_snapshots_fuera_de_mapa.
                    from core import pg_mirror
                    pg_mirror.write_native("options_snapshot", ["symbol"], [
                        {"symbol": d.get("symbol"), "tipo": d.get("tipo"),
                         "vence": d.get("vence"), "updated_at": d.get("updated_at"),
                         "data": pg_mirror.doc_iso(d)}
                        for d in docs if d.get("symbol")
                    ])

            except Exception as e:
                logger.error(f"Error en batch_snapshot_loop: {e}")

    def _flush_trades_loop(self):
        """Thread dedicado: persiste los trades en Postgres (mercado.options_data)
        en lotes de 1s, sin bloquear el hilo del WebSocket."""
        while True:
            time.sleep(1.0)
            self._flush_trades()

    def _flush_trades(self):
        """Drena el buffer, calcula Greeks y escribe el lote.

        Lo llama el loop cada 1s y TAMBIÉN el apagado del motor (para no perder el
        último buffer). No levanta nunca: corre en un hilo vital, y una excepción
        acá mataría el proceso entero.
        """
        if not self.trade_buffer:
            return
        with self._buffer_lock:
            batch = self.trade_buffer[:]
            self.trade_buffer = []
        try:
            trades = []
            for ticker, state_copy, server_time in batch:
                preparado = self._preparar_trade(ticker, state_copy, server_time)
                if preparado:
                    trades.append(preparado)
            self.writer.guardar_operaciones(trades)
        except Exception as e:
            logger.error(f"Error en flush de trades: {e}")

    def _preparar_trade(self, ticker, state_copy, server_time):
        """Calcula Griegas del trade y arma el registro para mercado.options_data.
        Devuelve None si el trade no se persiste (sin spot, o símbolo fuera del mapa)."""
        S = self.market_state.get(self.spot_symbol, {}).get('last', 0)
        if S <= 0:
            return None

        # El mapa puede haber rotado (refrescar_mapa) entre el encolado y el flush.
        info = self.mapa_opciones.get(ticker)
        if not info:
            return None

        K = info['strike']
        T = max((datetime.strptime(info['vence'], "%Y%m%d") - datetime.now()).days, 1) / 365.0

        vi = calc_intrinseco(S, K, info['tipo'])
        p_iv = state_copy['last'] if state_copy['last'] > vi else vi + 0.1

        griegas = None
        try:
            iv = find_iv(p_iv, S, K, T, self.tasa, info['tipo'])
            if iv > 0:
                g = bs_greeks(S, K, T, self.tasa, iv, info['tipo'])
                griegas = {
                    "iv":    round(iv, 4),
                    "delta": round(g["delta"], 3),
                    "gamma": round(g["gamma"], 4),
                    "vega":  round(g["vega"], 2),
                    "theta": round(g["theta"], 2)
                }
        except Exception:
            pass

        state_copy['strike'] = K
        state_copy['tipo']   = info['tipo']
        state_copy['spot']   = S
        return {"symbol": ticker, "data": state_copy,
                "server_time": server_time, "griegas": griegas}


# ==========================================
# ORQUESTADOR (sin UI)
# ==========================================
def run():
    global _running
    if not inicializar_sesion():
        return

    engine = OptionsEngine()
    # Resiliencia: si el padrón aún no tiene opciones (pre-mercado / OPEX sin nuevo
    # vencimiento listado), NO salir → reintentar adentro hasta que ROFEX las publique.
    # Antes el `return` mataba el proceso y systemd lo reiniciaba en LOOP (525 restarts
    # el 2026-06-16, pre-apertura). El batch_snapshot_loop ya está corriendo (no escribe
    # nada con estado vacío) → reusamos el mismo engine en vez de reconstruir (no leaka hilos).
    intentos = 0
    while _running and not engine.mapa_opciones:
        intentos += 1
        logger.warning(
            f"No se encontraron opciones de GGAL (intento {intentos}) — reintento en 60s "
            f"(pre-mercado / ROFEX aún no listó el vencimiento)."
        )
        for _ in range(60):
            if not _running:
                return
            time.sleep(1)
        engine.mapa_opciones, engine.agrupacion_strikes = engine._generar_maestra()
        if engine.mapa_opciones:
            engine._inicializar_estado_memoria()
            logger.info(f"✅ {len(engine.mapa_opciones)} opciones GGAL encontradas tras reintento.")
    if not engine.mapa_opciones:
        return  # _running pasó a False durante la espera → apagado limpio (no es crash)

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

    lanzar_hilo_vital(_refresh_loop, "refresh_loop")

    while _running:
        time.sleep(1)

    ws_manager.cerrar_ws()
    engine._flush_trades()  # último lote: el hilo de flush puede estar en su sleep
    logger.info("Motor apagado.")


if __name__ == "__main__":
    run()
