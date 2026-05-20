import logging
import os
import queue
import threading
import time
import traceback
from collections import deque
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pyRofex
from pymongo import UpdateOne

from core.mongo import get_mongo_client

# --- TUS MANAGERS DE INFRAESTRUCTURA ---
from core.rofex_session import inicializar_sesion
from core.websocket import WebSocketManager
from engines._curvas_loader import cargar_tickers_ordenados

logger = logging.getLogger("MotorValores")

ART = ZoneInfo("America/Argentina/Buenos_Aires")

VOLUME_BUCKET_SIZES = {
    "MERV - XMEV - TZXM6 - 24hs": 1056003093,
    "MERV - XMEV - S17A6 - 24hs": 1269958754, "MERV - XMEV - S30A6 - 24hs": 205979378,
    "MERV - XMEV - S29Y6 - 24hs": 384357785, "MERV - XMEV - T30J6 - 24hs": 291213720,
    "MERV - XMEV - S31L6 - 24hs": 93799093, "MERV - XMEV - S31G6 - 24hs": 39515420,
    "MERV - XMEV - S30O6 - 24hs": 30698089, "MERV - XMEV - S30N6 - 24hs": 25196577,
    "MERV - XMEV - T15E7 - 24hs": 157290278, "MERV - XMEV - T30A7 - 24hs": 438883807,
    "MERV - XMEV - T31Y7 - 24hs": 98359279, "MERV - XMEV - T30J7 - 24hs": 21615689,
    "MERV - XMEV - TY30P - 24hs": 26615939, "MERV - XMEV - X15Y6 - 24hs": 113153036,
    "MERV - XMEV - X29Y6 - 24hs": 426532800, "MERV - XMEV - TZX26 - 24hs": 363769490,
    "MERV - XMEV - X31L6 - 24hs": 67044105, "MERV - XMEV - TX26 - 24hs": 139822955,
    "MERV - XMEV - TZXO6 - 24hs": 208287938, "MERV - XMEV - X30N6 - 24hs": 80774915,
    "MERV - XMEV - TZXD6 - 24hs": 262315858, "MERV - XMEV - TZXM7 - 24hs": 176877365,
    "MERV - XMEV - TZXY7 - 24hs": 133934, "MERV - XMEV - TZX27 - 24hs": 9495066,
    "MERV - XMEV - TX28 - 24hs": 23072063, "MERV - XMEV - TZXD7 - 24hs": 156324283,
    "MERV - XMEV - TZX28 - 24hs": 172279837, "MERV - XMEV - DICP - 24hs": 27262545,
    "MERV - XMEV - PARP - 24hs": 4436050
}

# ==========================================
# 1. EL CEREBRO: MicrostructureEngine (INTACTO)
# ==========================================
class MicrostructureEngine:
    def __init__(self, tickers):
        self.tickers = tickers
        self.tick_queue = queue.Queue()
        self.trade_buffer = []
        self._buffer_lock = threading.Lock()
        self.market_state = {
            t: {
                "book": {"bids": [], "offers": []},
                "last_nv": 0.0,
                "last_price":    0.0,
                "open_price":    0.0,
                "high_price":    0.0,
                "low_price":     0.0,
                "closing_price": 0.0,
                "closed_vpins": deque(maxlen=50),
                "vpin_stats": {"current_buy_vol": 0, "current_sell_vol": 0, "last_vpin": 0.0},
                "daily_financials": {"total_money": 0.0, "buy_money": 0.0, "sell_money": 0.0, "total_nominals": 0.0},
                "hourly_stats": {h: {"buy": 0.0, "sell": 0.0, "total": 0.0} for h in range(10, 18)}
            } for t in self.tickers
        }
        try:
            self.mongo_client = get_mongo_client()
            self.db = self.mongo_client["Trading"]
            self.col_trades = self.db["TimeSales"]
            self.col_snapshot = self.db["MarketSnapshot"]
        except Exception as e:
            print(f"Error conectando a Mongo en main_ts: {e}")
            self.col_trades = None
            self.col_snapshot = None

        self._arranque_en_frio()
        threading.Thread(target=self._worker_loop, daemon=True).start()
        threading.Thread(target=self._flush_loop, daemon=True).start()
        threading.Thread(target=self._snapshot_loop, daemon=True).start()

    def _arranque_en_frio(self):
        # 1) Recuperar OPEN/HIGH/LOW reales desde el REST API de Rofex
        for ticker in self.tickers:
            try:
                md = pyRofex.get_market_data(
                    ticker,
                    entries=[
                        pyRofex.MarketDataEntry.OPENING_PRICE,
                        pyRofex.MarketDataEntry.HIGH_PRICE,
                        pyRofex.MarketDataEntry.LOW_PRICE,
                        pyRofex.MarketDataEntry.CLOSING_PRICE,
                        pyRofex.MarketDataEntry.LAST,
                    ]
                )
                data = md.get("marketData", {})
                st = self.market_state[ticker]
                if data.get("OP"):
                    st["open_price"] = float(data["OP"])
                if data.get("HI"):
                    st["high_price"] = float(data["HI"])
                if data.get("LO"):
                    st["low_price"] = float(data["LO"])
                if data.get("CL"):
                    st["closing_price"] = float(data["CL"])
                la = data.get("LA")
                if la and la.get("price"):
                    st["last_price"] = float(la["price"])
            except Exception as e:
                print(f"⚠️ No se pudo obtener market data REST para {ticker}: {e}")

        # 2) Reconstruir financials del día desde trades en MongoDB.
        # Una sola query con $in en vez de N find secuenciales (antes N+1).
        if self.col_trades is None: return
        _art_now = datetime.utcnow() - timedelta(hours=3)
        inicio = _art_now.replace(hour=0, minute=0, second=0, microsecond=0)
        cursor = self.col_trades.find(
            {"ticker": {"$in": list(self.tickers)}, "timestamp": {"$gte": inicio}},
            {"_id": 0, "ticker": 1, "price": 1, "size": 1, "side": 1, "timestamp": 1},
        )
        for doc in cursor:
            st = self.market_state.get(doc.get("ticker"))
            if not st:
                continue
            px, sz, sd = doc.get("price", 0), doc.get("size", 0), doc.get("side", "MID")
            cash = (px / 100.0) * sz
            st["daily_financials"]["total_nominals"] += sz
            st["daily_financials"]["total_money"] += cash
            if sd == "BUY":
                st["daily_financials"]["buy_money"] += cash
            elif sd == "SELL":
                st["daily_financials"]["sell_money"] += cash
            h = doc["timestamp"].hour
            if 10 <= h <= 17:
                st["hourly_stats"][h]["total"] += cash
                if sd == "BUY":
                    st["hourly_stats"][h]["buy"] += cash
                elif sd == "SELL":
                    st["hourly_stats"][h]["sell"] += cash

    def add_ticker(self, ticker):
        """Agrega un ticker en runtime (adhoc subscriptions).

        Inicializa la entrada en `market_state` con el mismo shape que el
        constructor, y lo suma a `self.tickers`. Idempotente — si ya
        estaba, devuelve False sin tocar nada. El llamador (adhoc
        watcher) hace después la suscripción pyRofex en el WS abierto.
        """
        if ticker in self.market_state:
            return False
        self.market_state[ticker] = {
            "book": {"bids": [], "offers": []},
            "last_nv": 0.0,
            "last_price":    0.0,
            "open_price":    0.0,
            "high_price":    0.0,
            "low_price":     0.0,
            "closing_price": 0.0,
            "closed_vpins": deque(maxlen=50),
            "vpin_stats": {"current_buy_vol": 0, "current_sell_vol": 0, "last_vpin": 0.0},
            "daily_financials": {"total_money": 0.0, "buy_money": 0.0, "sell_money": 0.0, "total_nominals": 0.0},
            "hourly_stats": {h: {"buy": 0.0, "sell": 0.0, "total": 0.0} for h in range(10, 18)},
        }
        if ticker not in self.tickers:
            # self.tickers puede ser list o set según cómo lo armó el caller.
            try:
                self.tickers.append(ticker)
            except AttributeError:
                self.tickers.add(ticker)
        return True

    def update_price(self, ticker, data):
        self.tick_queue.put((ticker, data))

    def _worker_loop(self):
        while True:
            try:
                ticker, data = self.tick_queue.get(timeout=0.5)
                try:
                    self._procesar_tick_logica(ticker, data)
                except Exception:
                    logger.error(f"Error procesando tick {ticker}:\n{traceback.format_exc()}")
            except queue.Empty:
                pass

    def _flush_loop(self):
        """Thread dedicado: persiste trades en MongoDB sin bloquear el worker."""
        while True:
            time.sleep(1.0)
            if not self.trade_buffer or self.col_trades is None:
                continue
            with self._buffer_lock:
                batch = self.trade_buffer[:]
                self.trade_buffer = []
            try:
                self.col_trades.insert_many(batch, ordered=False)
            except Exception as e:
                print(f"Error flush trades: {e}")

    def _procesar_tick_logica(self, ticker, data):
        # Defensivo: race entre WS push y add_ticker (adhoc). Si llega un
        # tick antes de que la entrada exista, lo dropeamos — el próximo
        # tick ya tendrá el state listo.
        st = self.market_state.get(ticker)
        if st is None:
            return
        if "BI" in data: st["book"]["bids"] = data["BI"][:5]
        if "OF" in data: st["book"]["offers"] = data["OF"][:5]
        if "EV" in data and data["EV"] is not None: st["daily_financials"]["total_money"] = float(data["EV"])
        if "NV" in data and data["NV"] is not None: st["daily_financials"]["total_nominals"] = float(data["NV"])
        def _to_float(v):
            if isinstance(v, dict): v = v.get("price", 0)
            try:
                return float(v) if v else 0.0
            except (TypeError, ValueError):
                return 0.0
        if data.get("OP"): st["open_price"]    = _to_float(data["OP"])
        if data.get("HI"): st["high_price"]    = _to_float(data["HI"])
        if data.get("LO"): st["low_price"]     = _to_float(data["LO"])
        if data.get("CL"): st["closing_price"] = _to_float(data["CL"])

        last, nv = data.get("LA"), data.get("NV")
        if last and nv is not None:
            px, ts_ms = float(last.get("price", 0)), last.get("date", 0)
            if px <= 0: return
            if st["last_nv"] == 0:
                st["last_nv"], st["last_price"] = nv, px
                return

            if nv > st["last_nv"]:
                sz = nv - st["last_nv"]
                st["last_nv"] = nv

                b1 = st["book"]["bids"][0]["price"] if st["book"]["bids"] else 0
                o1 = st["book"]["offers"][0]["price"] if st["book"]["offers"] else 0
                side = "MID"
                if px >= o1 and o1 > 0:
                    side = "BUY"
                elif px <= b1 and b1 > 0:
                    side = "SELL"
                elif px > st["last_price"]:
                    side = "BUY"
                elif px < st["last_price"]:
                    side = "SELL"
                st["last_price"] = px

                cash = (px / 100.0) * sz

                bucket_limit = VOLUME_BUCKET_SIZES.get(ticker, 1000000)
                rem_size = sz
                while rem_size > 0:
                    fill = st["vpin_stats"]["current_buy_vol"] + st["vpin_stats"]["current_sell_vol"]
                    chunk = min(rem_size, bucket_limit - fill)
                    if side == "BUY":
                        st["vpin_stats"]["current_buy_vol"] += chunk
                    elif side == "SELL":
                        st["vpin_stats"]["current_sell_vol"] += chunk
                    rem_size -= chunk
                    if (st["vpin_stats"]["current_buy_vol"] + st["vpin_stats"]["current_sell_vol"]) >= bucket_limit:
                        v_diff = abs(st["vpin_stats"]["current_buy_vol"] - st["vpin_stats"]["current_sell_vol"])
                        vpin_val = v_diff / bucket_limit
                        st["closed_vpins"].append(vpin_val)
                        st["vpin_stats"]["last_vpin"] = sum(st["closed_vpins"]) / len(st["closed_vpins"])
                        st["vpin_stats"]["current_buy_vol"], st["vpin_stats"]["current_sell_vol"] = 0, 0

                st["daily_financials"]["total_nominals"] += sz
                if side == "BUY":
                    st["daily_financials"]["buy_money"] += cash
                elif side == "SELL":
                    st["daily_financials"]["sell_money"] += cash

                dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC).astimezone(ART).replace(tzinfo=None)
                h = dt.hour
                if 10 <= h <= 17:
                    st["hourly_stats"][h]["total"] += cash
                    if side == "BUY":
                        st["hourly_stats"][h]["buy"] += cash
                    elif side == "SELL":
                        st["hourly_stats"][h]["sell"] += cash

                trade = {"timestamp": dt, "price": px, "size": sz, "side": side, "money": cash}
                with self._buffer_lock:
                    self.trade_buffer.append({"ticker": ticker, **trade})

    def _calcular_metricas(self, ticker):
        """Calcula métricas del snapshot (shape reducido).

        Deprecado y removido: micro_price, spread, imbalance, total_money,
        buy_money, sell_money, vpin_prom, vpin_vivo, progreso, buy_b, sell_b.
        Eran útiles en el modo terminal original pero la app no los usa.
        Si en el futuro los necesita alguien, el state interno del engine
        los sigue calculando — solo no los persistimos.

        Los campos analíticos (TEA, TEM, duration, convexity, paridad) los
        escribe engines/curvas.py directamente en este mismo doc cuando
        enriquece trades. Acá no los tocamos.
        """
        st = self.market_state[ticker]
        fs = st["daily_financials"]

        return {
            "last_price":    st["last_price"],
            "open_price":    st["open_price"],
            "high_price":    st["high_price"],
            "low_price":     st["low_price"],
            "closing_price": st["closing_price"],
            "vwap":           (fs["total_money"] / fs["total_nominals"] * 100) if fs["total_nominals"] > 0 else 0,
            "total_nominals": fs["total_nominals"],
        }

    def _snapshot_loop(self):
        """
        Escribe el estado completo de todos los tickers a MarketSnapshot cada 1s.
        Un único bulk_write reemplaza N round-trips individuales a Atlas.
        """
        while True:
            time.sleep(1)
            if self.col_snapshot is None:
                continue
            try:
                ts = datetime.now(UTC)
                ops = []
                # Copia defensiva — el adhoc_watcher puede agregar tickers
                # concurrentemente con este loop.
                for ticker in list(self.tickers):
                    st = self.market_state[ticker]
                    metricas = self._calcular_metricas(ticker)

                    # Defensivo: si no tenemos last_price real en memoria
                    # (p.ej. _arranque_en_frio falló silencioso para este
                    # ticker, o nunca recibimos tick del WS), no pisamos
                    # el doc — preservamos lo que puso snapshot_rest o la
                    # sesión previa.
                    if metricas.get("last_price") is None:
                        continue

                    # UpdateOne $set parcial: solo los campos que este motor
                    # gobierna (book + métricas de precio del día). Los
                    # analíticos (metrics.TEA/TEM/duration/mod_duration/
                    # convexity/paridad) los actualiza engines/curvas.py con
                    # su propio $set y NO los tocamos acá. Cada motor escribe
                    # lo suyo, sin guardas mutuas.
                    ops.append(UpdateOne(
                        {"ticker": ticker},
                        {"$set": {
                            "updated_at":             ts,
                            "book.bids":              list(st["book"]["bids"]),
                            "book.offers":            list(st["book"]["offers"]),
                            "metrics.last_price":     metricas.get("last_price"),
                            "metrics.open_price":     metricas.get("open_price"),
                            "metrics.high_price":     metricas.get("high_price"),
                            "metrics.low_price":      metricas.get("low_price"),
                            "metrics.closing_price":  metricas.get("closing_price"),
                            "metrics.vwap":           metricas.get("vwap"),
                            "metrics.total_nominals": metricas.get("total_nominals"),
                        }},
                        upsert=True,
                    ))

                if ops:
                    self.col_snapshot.bulk_write(ops, ordered=False)

            except Exception as e:
                logger.error(f"Error escribiendo snapshots: {e}")


# ==========================================
# 2. ADHOC WATCHER — suscripciones dinámicas en runtime
# ==========================================

def _adhoc_watcher(engine, ws_manager, poll_s: int = 5):
    """Thread daemon: cada `poll_s` segundos, lee Trading.AdhocSubscriptions
    y suscribe vía pyRofex los tickers que NO están en `engine.tickers`.

    Las suscripciones pyRofex son aditivas (no rompen las existentes), así
    que se puede llamar `agregar_suscripciones` cuantas veces haga falta.
    """
    from core.adhoc_subscriptions import ensure_indexes, list_active_tickers

    try:
        ensure_indexes()
        logger.info("adhoc_watcher: índices TTL listos en Trading.AdhocSubscriptions")
    except Exception as e:
        logger.warning(f"adhoc_watcher: ensure_indexes falló: {e}")

    while True:
        try:
            time.sleep(poll_s)
            actuales = set(engine.tickers)
            adhoc = set(list_active_tickers())
            nuevos = [t for t in adhoc if t and t not in actuales]
            if not nuevos:
                continue
            logger.info(
                f"adhoc_watcher: suscribiendo {len(nuevos)} tickers nuevos: {nuevos}"
            )
            for t in nuevos:
                engine.add_ticker(t)
            ws_manager.agregar_suscripciones(nuevos, depth=5)
        except Exception:
            logger.error(f"adhoc_watcher loop error:\n{traceback.format_exc()}")


# ==========================================
# 3. EL BUCLE PRINCIPAL (MODO MOTOR CIEGO)
# ==========================================
def run():
    print("🚀 Iniciando Motor de Escritura (Modo Headless / Sin Interfaz)...")
    if not inicializar_sesion(): return

    tickers = cargar_tickers_ordenados()
    print(f"📋 Tickers cargados desde Trading.Curvas + Extra + Adhoc: {len(tickers)}")
    if not tickers:
        print("❌ Sin tickers en Trading.Curvas. Abortando.")
        return

    engine = MicrostructureEngine(tickers)
    ws_manager = WebSocketManager(engine)

    try:
        # Iniciamos el WebSocket
        if ws_manager.iniciar_ws(tickers, depth=5):
            print("✅ Conectado a Rofex. Escuchando y guardando datos...")
            # Thread daemon que polea Trading.AdhocSubscriptions y suma
            # tickers dinámicamente (el user agrega un panel en el
            # Dashboard de Operar y el motor lo suscribe en ≤5s).
            threading.Thread(
                target=_adhoc_watcher,
                args=(engine, ws_manager),
                daemon=True,
            ).start()
            # Como borramos la app visual, necesitamos este bucle infinito
            # para que el script no se cierre y el thread de Rofex siga vivo.
            while True:
                time.sleep(1)

    except KeyboardInterrupt:
        print("\n🛑 Apagado manual detectado.")
    except Exception:
        print("\n❌ EL PROGRAMA CRASHEÓ:")
        traceback.print_exc()

    finally:
        print("\n🛑 Cerrando WebSocket y limpiando hilos...")
        try:
            pyRofex.close_websocket_connection()
        except Exception:
            pass
        os._exit(0)

if __name__ == "__main__":
    run()