import logging
import os
import queue
import threading
import time
import traceback
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pyRofex

from core import pg_mirror

# --- TUS MANAGERS DE INFRAESTRUCTURA ---
from core.rofex_session import inicializar_sesion
from core.threads import lanzar_hilo_vital
from core.websocket import WebSocketManager
from engines._curvas_loader import cargar_tickers_ordenados

logger = logging.getLogger("MotorValores")

ART = ZoneInfo("America/Argentina/Buenos_Aires")

# Cadencia de escritura del snapshot. El loop despierta cada 1s pero solo
# persiste cada SNAPSHOT_INTERVAL_S — reescribe el estado COMPLETO de todos
# los tickers, así que descartar iteraciones intermedias no pierde nada.
SNAPSHOT_INTERVAL_S = 5.0

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
                "daily_financials": {"total_money": 0.0, "buy_money": 0.0, "sell_money": 0.0, "total_nominals": 0.0},
                "hourly_stats": {h: {"buy": 0.0, "sell": 0.0, "total": 0.0} for h in range(10, 18)}
            } for t in self.tickers
        }
        # TimeSales y MarketSnapshot son SQL-native (mercado.*). Las
        # suscripciones adhoc viven en mercado.adhoc_subscriptions (core).
        # Retención de mercado.timesales (~7d) — 1 vez al arranque. El tape muestra solo
        # el día; no hace falta guardar más. SQL-only → prune incondicional.
        try:
            from core import pg_mirror
            pg_mirror.prune_native("mercado.timesales", "ts", 7)
        except Exception:
            pass

        self._arranque_en_frio()
        lanzar_hilo_vital(self._worker_loop, "worker_loop")
        lanzar_hilo_vital(self._flush_loop, "flush_loop")
        lanzar_hilo_vital(self._snapshot_loop, "snapshot_loop")

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

        # 2) Reconstruir financials del día desde los trades en SQL (mercado.timesales,
        # SQL-only desde 2026-06-22). Una sola query con ANY en vez de N find.
        _art_now = datetime.utcnow() - timedelta(hours=3)
        inicio = _art_now.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            from core.postgres import get_pool
            with get_pool().connection() as _cn, _cn.cursor() as _cur:
                _cur.execute(
                    "SELECT ticker, price, size, side, ts FROM mercado.timesales "
                    "WHERE ticker = ANY(%s) AND ts >= %s",
                    (list(self.tickers), inicio),
                )
                trades_hoy = _cur.fetchall()
        except Exception as e:
            print(f"⚠️ No se pudo reconstruir financials desde SQL: {e}")
            return
        for ticker_t, px, sz, sd, ts in trades_hoy:
            st = self.market_state.get(ticker_t)
            if not st:
                continue
            px = float(px or 0)
            sz = float(sz or 0)
            sd = sd or "MID"
            cash = (px / 100.0) * sz
            st["daily_financials"]["total_nominals"] += sz
            st["daily_financials"]["total_money"] += cash
            if sd == "BUY":
                st["daily_financials"]["buy_money"] += cash
            elif sd == "SELL":
                st["daily_financials"]["sell_money"] += cash
            h = ts.hour
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
        """Thread dedicado: persiste los trades en Postgres (mercado.timesales),
        en lotes de 1s, sin bloquear el worker."""
        while True:
            time.sleep(1.0)
            if not self.trade_buffer:
                continue
            with self._buffer_lock:
                batch = self.trade_buffer[:]
                self.trade_buffer = []
            # SQL-ONLY (2026-06-22): TimeSales ya NO se escribe en Mongo. Solo los campos
            # del tape (sin los enriquecidos TEA/TEM/duration que el tape no muestra).
            try:
                from core import pg_mirror
                pg_mirror.append_native("mercado.timesales", [
                    {"ticker": t.get("ticker"), "ts": t.get("timestamp"),
                     "price": t.get("price"), "size": t.get("size"),
                     "side": t.get("side"), "money": t.get("money")}
                    for t in batch if t.get("ticker") and t.get("timestamp")
                ])
            except Exception as e:
                print(f"Error flush trades SQL: {e}")

    def _procesar_tick_logica(self, ticker, data):
        # Defensivo: race entre WS push y add_ticker (adhoc). Si llega un
        # tick antes de que la entrada exista, lo dropeamos — el próximo
        # tick ya tendrá el state listo.
        st = self.market_state.get(ticker)
        if st is None:
            return
        if "BI" in data: st["book"]["bids"] = (data["BI"] or [])[:5]
        if "OF" in data: st["book"]["offers"] = (data["OF"] or [])[:5]
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
        Eran útiles en el modo terminal original pero la app no los usa. El
        VPIN además dejó de calcularse (no lo leía nadie: ni el snapshot, ni
        la API, ni el front). total_money/total_nominals sí siguen vivos en el
        state porque alimentan el vwap.

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
        Escribe el estado completo de todos los tickers a mercado.market_snapshot
        (SQL) cada SNAPSHOT_INTERVAL_S.

        El throttle se aplica ANTES de armar el payload: el loop despierta cada 1s
        pero solo construye las rows en la iteración que efectivamente persiste.
        """
        ultimo_flush = 0.0
        while True:
            time.sleep(1)
            try:
                ahora = time.monotonic()
                if ahora - ultimo_flush < SNAPSHOT_INTERVAL_S:
                    continue

                ts = datetime.now(UTC)
                # SQL-only (mercado.market_snapshot): ya NO se escribe Trading.MarketSnapshot.
                pg_rows = []
                # Copia defensiva — el adhoc_watcher puede agregar tickers
                # concurrentemente con este loop.
                for ticker in list(self.tickers):
                    st = self.market_state[ticker]
                    metricas = self._calcular_metricas(ticker)

                    # Upsert columnar parcial: solo los campos que este motor
                    # gobierna (book + métricas de precio del día). Los analíticos
                    # (tea/tem/duration/mod_duration/convexity/paridad) los escribe
                    # engines/curvas.py sobre sus propias columnas — el upsert SQL
                    # actualiza SOLO las columnas presentes en cada row, sin pisar
                    # las del otro motor.
                    row = {
                        "ticker":     ticker,
                        "book":       {"bids":   list(st["book"]["bids"]),
                                       "offers": list(st["book"]["offers"])},
                        "updated_at": ts,
                    }

                    # Defensivo: si no tenemos last_price real en memoria (p.ej.
                    # _arranque_en_frio falló silencioso para este ticker, o todavía
                    # no llegó ningún trade), OMITIMOS las columnas de precio en vez
                    # de escribir ceros — el upsert parcial preserva así el cierre
                    # previo. El book sí se escribe siempre: es live y hay tickers
                    # (adhoc, ilíquidos) que tienen puntas sin haber operado nunca.
                    if (metricas.get("last_price") or 0) > 0:
                        row.update({
                            "last_price":     metricas.get("last_price"),
                            "open_price":     metricas.get("open_price"),
                            "high_price":     metricas.get("high_price"),
                            "low_price":      metricas.get("low_price"),
                            "closing_price":  metricas.get("closing_price"),
                            "vwap":           metricas.get("vwap"),
                            "total_nominals": metricas.get("total_nominals"),
                        })

                    pg_rows.append(row)

                if pg_rows:
                    ultimo_flush = ahora
                    pg_mirror.write_snapshot("market_snapshot", ["ticker"], pg_rows)

            except Exception as e:
                logger.error(f"Error escribiendo snapshots: {e}")


# ==========================================
# 2. ADHOC WATCHER — suscripciones dinámicas en runtime
# ==========================================

def _adhoc_watcher(engine, ws_manager, poll_s: int = 5):
    """Thread daemon: cada `poll_s` segundos, lee mercado.adhoc_subscriptions
    y suscribe vía pyRofex los tickers que NO están en `engine.tickers`.

    Las suscripciones pyRofex son aditivas (no rompen las existentes), así
    que se puede llamar `agregar_suscripciones` cuantas veces haga falta.
    """
    from core.adhoc_subscriptions import ensure_indexes, list_active_tickers

    try:
        ensure_indexes()
        logger.info("adhoc_watcher: listo (mercado.adhoc_subscriptions, SQL)")
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