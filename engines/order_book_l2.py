"""Motor dedicado a captura full del Limit Order Book L2 (depth 5).

Para los tickers en config.TICKERS_BOOK_FULL: cada cambio en bids u
offers se persiste como un doc nuevo en Trading.OrderBookL2 (Time Series
Collection). Sin ReplaceOne, sin sampling, sin agregación — append puro.

NO comparte estado, ni colecciones, ni sesión rofex con engines/valores.py:
- motor_rofex (engines/valores.py) sigue alimentando TimeSales y MarketSnapshot
  como hoy. Los trades de los tickers en TICKERS_BOOK_FULL se siguen
  capturando ahí (no se duplican acá).
- Este motor abre su propia sesión pyRofex y solo se suscribe a entries
  BIDS y OFFERS (no LA/NV/OHLC) — tráfico WS estrictamente mínimo.

Dedup: cada update WS llega con el book completo (5 niveles). El motor
compara contra el último estado persistido en RAM; si bids u offers
difieren en algún price/size, inserta. Si vienen idénticos (replays
del broker), skip.

Persistencia: buffer en memoria + thread de flush con bulk_insert cada
500ms a Trading.OrderBookL2.

Ejecutar:
    python -m engines.order_book_l2
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

from config import TICKERS_BOOK_FULL
from core.mongo import get_mongo_client
from core.rofex_session import inicializar_sesion
from core.websocket import WebSocketManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("MotorOrderBookL2")

DB_NAME = "Trading"
COL_NAME = "OrderBookL2"
DEPTH = 5
FLUSH_INTERVAL_S = 0.5

# Solo BIDS y OFFERS — no nos interesa LA/NV/OHLC en este motor.
# Los trades los captura motor_rofex (engines/valores.py) en TimeSales.
ENTRIES_BOOK_ONLY = [
    pyRofex.MarketDataEntry.BIDS,
    pyRofex.MarketDataEntry.OFFERS,
]

_running = True


def _handle_signal(sig, frame):
    global _running
    logger.info("Señal de cierre recibida — flusheo y apago.")
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def _book_key(book_data):
    """Tupla hashable de (price, size) por nivel para dedup rápida."""
    return tuple(
        (lvl.get("price"), lvl.get("size"))
        for lvl in (book_data or [])[:DEPTH]
    )


class OrderBookL2Engine:
    def __init__(self, tickers):
        self.tickers = tickers
        self.state = {
            t: {
                "bids":       [],
                "offers":     [],
                "bids_key":   None,
                "offers_key": None,
            }
            for t in tickers
        }
        self._buffer: list[dict] = []
        self._buffer_lock = threading.Lock()

        self.client = get_mongo_client()
        self.col = self.client[DB_NAME][COL_NAME]

        threading.Thread(target=self._flush_loop, daemon=True).start()

    def update_price(self, ticker: str, data: dict):
        """Handler invocado por WebSocketManager por cada mensaje WS."""
        s = self.state.get(ticker)
        if s is None:
            return

        changed = False

        if "BI" in data:
            bids = data["BI"][:DEPTH]
            new_key = _book_key(bids)
            if new_key != s["bids_key"]:
                s["bids"] = bids
                s["bids_key"] = new_key
                changed = True

        if "OF" in data:
            offers = data["OF"][:DEPTH]
            new_key = _book_key(offers)
            if new_key != s["offers_key"]:
                s["offers"] = offers
                s["offers_key"] = new_key
                changed = True

        if not changed:
            return

        # Doc representa el estado del book post-cambio. Si solo cambió un
        # lado, el otro va con su último valor conocido.
        doc = {
            "ts":     datetime.now(UTC),
            "ticker": ticker,
            "bids":   list(s["bids"]),
            "offers": list(s["offers"]),
        }
        with self._buffer_lock:
            self._buffer.append(doc)

    def _flush_loop(self):
        """Cada FLUSH_INTERVAL_S vuelca el buffer a Mongo en bulk."""
        while True:
            time.sleep(FLUSH_INTERVAL_S)
            try:
                with self._buffer_lock:
                    if not self._buffer:
                        continue
                    batch = self._buffer
                    self._buffer = []
                self.col.insert_many(batch, ordered=False)
            except Exception:
                logger.error("Flush falló:\n%s", traceback.format_exc())


def run():
    logger.info("Motor OrderBookL2 iniciando…")

    tickers = sorted(TICKERS_BOOK_FULL)
    if not tickers:
        logger.warning("TICKERS_BOOK_FULL está vacío — nada que capturar. Saliendo.")
        return

    if not inicializar_sesion():
        logger.error("No se pudo inicializar sesión rofex — abortando.")
        sys.exit(1)

    engine = OrderBookL2Engine(tickers)
    ws = WebSocketManager(engine)

    if not ws.iniciar_ws(tickers, depth=DEPTH, entries=ENTRIES_BOOK_ONLY):
        logger.error("No pude iniciar WS — abortando.")
        sys.exit(1)

    logger.info(
        "WS arriba. Suscripto a %d ticker(s) con depth=%d, entries=BIDS+OFFERS. "
        "Flush %ss → %s.%s",
        len(tickers), DEPTH, FLUSH_INTERVAL_S, DB_NAME, COL_NAME,
    )

    try:
        while _running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        # Último flush antes de cerrar
        try:
            with engine._buffer_lock:
                pending = engine._buffer
                engine._buffer = []
            if pending:
                engine.col.insert_many(pending, ordered=False)
                logger.info("Flush final: %d doc(s) volcados.", len(pending))
        except Exception:
            logger.exception("Flush final falló")
        try:
            ws.cerrar_ws()
        except Exception:
            pass


if __name__ == "__main__":
    run()
