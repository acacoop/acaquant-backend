"""Motor dedicado a captura del último precio para tickers de tenencia.

Suscribe via pyRofex WS a los activos que la mesa tiene en posición HOY
(Valuaciones.AuM último snapshot + boletos del día) y mantiene su
`last_price` y `closing_price` actualizado en Trading.PortfolioSnapshot.

NO comparte estado con engines/valores.py:
- Sesión pyRofex propia (segunda conexión, igual que motor_options).
- Suscripción reducida a entries [LAST, CLOSING_PRICE] — sin BIDS,
  OFFERS, OHLC, NV. Tráfico WS estrictamente mínimo.
- NO popula Trading.TimeSales — los trades históricos los captura
  motor_rofex para los tickers de Trading.Curvas.

Persistencia: bulk_write con UpdateOne+upsert a Trading.PortfolioSnapshot
cada 1s. Solo escribe los tickers que recibieron un cambio en el último
intervalo (dirty flag). Schema:
  {
    ticker:        "MERV - XMEV - AL30 - 24hs",
    last_price:    float,
    closing_price: float,
    updated_at:    datetime UTC
  }

Refresh dinámico: thread separado cada 60 min revisa el universo de
tenencia + boletos del día, agrega los tickers nuevos via
WebSocketManager.agregar_suscripciones (aditivo, no reabre WS). Cada
refresh persiste un doc en Manager.PortfolioSnapshotLog para auditoría.

Ejecutar:
    python -m engines.portfolio_snapshot
"""
from __future__ import annotations

import logging
import signal
import sys
import threading
import time
import traceback
from datetime import UTC, datetime

import pyRofex
from pymongo import UpdateOne

from core.mongo import get_mongo_client
from core.rofex_session import inicializar_sesion
from core.websocket import WebSocketManager
from engines._universo_portfolio import tickers_de_tenencia

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("MotorPortfolioSnapshot")

DB_NAME = "Trading"
COL_NAME = "PortfolioSnapshot"
LOG_DB_NAME = "Manager"
LOG_COL_NAME = "PortfolioSnapshotLog"

SNAPSHOT_INTERVAL_S = 1.0
REFRESH_INTERVAL_S = 60 * 60   # 1 hora — alineado con cron de operaciones

ENTRIES_LIVE = [
    pyRofex.MarketDataEntry.LAST,
    pyRofex.MarketDataEntry.CLOSING_PRICE,
]

_running = True


def _handle_signal(sig, frame):
    global _running
    logger.info("Señal de cierre recibida — flusheo y apago.")
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def _to_float(v) -> float | None:
    if isinstance(v, dict):
        v = v.get("price")
    if v is None:
        return None
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


class PortfolioSnapshotEngine:
    def __init__(self, tickers: set[str]):
        self._state_lock = threading.Lock()
        # state[ticker] = {"last_price": float|None, "closing_price": float|None, "dirty": bool}
        self.state: dict[str, dict] = {}
        for t in tickers:
            self.state[t] = {"last_price": None, "closing_price": None, "dirty": False}
        self.tickers: set[str] = set(tickers)

        self.client = get_mongo_client()
        self.col = self.client[DB_NAME][COL_NAME]
        self.col_log = self.client[LOG_DB_NAME][LOG_COL_NAME]

        threading.Thread(target=self._snapshot_loop, daemon=True).start()

    # ── Handler invocado por WebSocketManager ──────────────────────────
    def update_price(self, ticker: str, data: dict):
        with self._state_lock:
            s = self.state.get(ticker)
            if s is None:
                # Tick para un ticker que aún no agregamos (race con refresh).
                # Lo creamos al vuelo — el agregar_tickers lo hubiera hecho igual.
                s = {"last_price": None, "closing_price": None, "dirty": False}
                self.state[ticker] = s
                self.tickers.add(ticker)

        try:
            last = data.get("LA")
            if last:
                px = _to_float(last)
                if px is not None and px != s["last_price"]:
                    s["last_price"] = px
                    s["dirty"] = True
            cl = data.get("CL")
            if cl is not None:
                px = _to_float(cl)
                if px is not None and px != s["closing_price"]:
                    s["closing_price"] = px
                    s["dirty"] = True
        except Exception:
            logger.error("update_price error %s:\n%s", ticker, traceback.format_exc())

    # ── Suscripción de tickers nuevos ──────────────────────────────────
    def agregar_tickers(self, nuevos: list[str]) -> list[str]:
        """Inicializa state para tickers nuevos. Devuelve los que efectivamente
        se agregaron (los que ya estaban se ignoran). Llamar ANTES de
        ws.agregar_suscripciones para evitar KeyError si llega un tick."""
        agregados = []
        with self._state_lock:
            for t in nuevos:
                if t not in self.state:
                    self.state[t] = {"last_price": None, "closing_price": None, "dirty": False}
                    self.tickers.add(t)
                    agregados.append(t)
        return agregados

    def universo_actual(self) -> set[str]:
        with self._state_lock:
            return self.tickers.copy()

    # ── Persistencia ───────────────────────────────────────────────────
    def _snapshot_loop(self):
        """Bulk_write cada SNAPSHOT_INTERVAL_S de los tickers que cambiaron."""
        while True:
            time.sleep(SNAPSHOT_INTERVAL_S)
            try:
                ts = datetime.now(UTC)
                ops: list[UpdateOne] = []
                with self._state_lock:
                    snapshot_items = list(self.state.items())
                for ticker, s in snapshot_items:
                    if not s.get("dirty"):
                        continue
                    sets = {"updated_at": ts}
                    if s.get("last_price") is not None:
                        sets["last_price"] = s["last_price"]
                    if s.get("closing_price") is not None:
                        sets["closing_price"] = s["closing_price"]
                    ops.append(UpdateOne(
                        {"ticker": ticker},
                        {"$set": sets},
                        upsert=True,
                    ))
                    s["dirty"] = False
                if ops:
                    self.col.bulk_write(ops, ordered=False)
            except Exception:
                logger.error("Snapshot loop falló:\n%s", traceback.format_exc())


def _refresh_loop(engine: PortfolioSnapshotEngine, ws: WebSocketManager):
    """Cada REFRESH_INTERVAL_S detecta tickers nuevos y los suscribe.

    Orden estricto:
      1) engine.agregar_tickers(nuevos) → inicializa state local.
      2) ws.agregar_suscripciones(nuevos, entries=...) → pyRofex.
      3) Persiste log a Manager.PortfolioSnapshotLog.

    Si pyRofex falla, motor sigue corriendo con el universo previo.
    """
    while _running:
        time.sleep(REFRESH_INTERVAL_S)
        if not _running:
            break
        try:
            actuales = engine.universo_actual()
            validados, sin_match = tickers_de_tenencia()
            nuevos = sorted(validados - actuales)
            if not nuevos and not sin_match:
                logger.info("refresh: sin cambios (universo=%d)", len(actuales))
                _persistir_log(engine, len(actuales), [], list(sin_match))
                continue
            if nuevos:
                agregados = engine.agregar_tickers(nuevos)
                try:
                    ws.agregar_suscripciones(agregados, depth=1, entries=ENTRIES_LIVE)
                    logger.info("refresh: +%d tickers (sample: %s)",
                                len(agregados), agregados[:5])
                except Exception:
                    logger.error("refresh: pyRofex subscribe falló — "
                                 "estado local agregado, motor sigue:\n%s",
                                 traceback.format_exc())
            else:
                agregados = []
            _persistir_log(engine, len(actuales) + len(agregados),
                           agregados, list(sin_match))
        except Exception:
            logger.error("refresh_loop tick falló:\n%s", traceback.format_exc())


def _persistir_log(engine: PortfolioSnapshotEngine, n_actuales: int,
                   nuevos: list[str], sin_match: list[str]):
    """Audit en Manager.PortfolioSnapshotLog. Si falla, warning y sigue."""
    try:
        engine.col_log.insert_one({
            "ts":           datetime.now(UTC),
            "n_actuales":   n_actuales,
            "n_nuevos":     len(nuevos),
            "nuevos":       nuevos,
            "n_sin_match":  len(sin_match),
            "sin_match":    sin_match[:50],   # cap por defensiva
        })
    except Exception:
        logger.warning("No pude persistir log de refresh — sigue.")


def run():
    logger.info("Motor PortfolioSnapshot iniciando…")
    if not inicializar_sesion():
        logger.error("No pude inicializar sesión rofex — abortando.")
        sys.exit(1)

    validados, sin_match = tickers_de_tenencia()
    if not validados:
        logger.warning("Universo de tenencia vacío. Esperá al próximo refresh.")
        # Igual arrancamos: el refresh thread va a re-intentar cada hora.

    engine = PortfolioSnapshotEngine(validados)
    ws = WebSocketManager(engine)

    if validados and not ws.iniciar_ws(sorted(validados), depth=1, entries=ENTRIES_LIVE):
        logger.error("No pude iniciar WS — abortando.")
        sys.exit(1)

    logger.info(
        "WS arriba. Universo inicial: %d tickers. Snapshot cada %ss → %s.%s. "
        "Refresh cada %ss. Sin match en pyRofex: %d.",
        len(validados), SNAPSHOT_INTERVAL_S, DB_NAME, COL_NAME,
        REFRESH_INTERVAL_S, len(sin_match),
    )
    _persistir_log(engine, len(validados), sorted(validados), list(sin_match))

    threading.Thread(target=_refresh_loop, args=(engine, ws), daemon=True).start()

    try:
        while _running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            ws.cerrar_ws()
        except Exception:
            pass


if __name__ == "__main__":
    run()
