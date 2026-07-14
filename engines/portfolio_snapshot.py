"""Motor dedicado a captura del último precio para tickers de tenencia.

Suscribe via pyRofex WS a los activos que la mesa tiene en posición HOY
(`portafolio.tenencia`, último snapshot + boletos del día) y mantiene su
`last_price` y `closing_price` actualizado en `valuaciones.portfolio_snapshot`.

NO comparte estado con engines/valores.py:
- Sesión pyRofex propia (segunda conexión, igual que motor_options).
- Suscripción reducida a entries [LAST, CLOSING_PRICE] — sin BIDS,
  OFFERS, OHLC, NV. Tráfico WS estrictamente mínimo.
- NO popula `mercado.timesales` — los trades históricos los captura
  motor_rofex para los tickers de `mercado.curvas`.

Persistencia: UPSERT por ticker (`pg_mirror.write_snapshot`) cada 1s. Solo
escribe los tickers que recibieron un cambio en el último intervalo (dirty
flag). Columnas: ticker (PK), last_price, closing_price, updated_at.

Refresh dinámico: thread separado cada 60 min revisa el universo de
tenencia + boletos del día, agrega los tickers nuevos via
WebSocketManager.agregar_suscripciones (aditivo, no reabre WS). Cada
refresh audita una fila en `manager.portfolio_snapshot_log`.

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

from core.pg_mirror import append_native, write_snapshot
from core.rofex_session import inicializar_sesion
from core.threads import lanzar_hilo_vital
from core.websocket import WebSocketManager
from engines._universo_portfolio import tickers_de_tenencia

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("MotorPortfolioSnapshot")

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

        # SQL-NATIVE (decomiso Mongo): precio live → valuaciones.portfolio_snapshot;
        # audit del refresh → manager.portfolio_snapshot_log.

        lanzar_hilo_vital(self._snapshot_loop, "snapshot_loop")

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

            # Mutar s["last_price"/"closing_price"/"dirty"] DENTRO del lock:
            # `_snapshot_loop` lee y resetea `dirty` desde otro thread — sin
            # el lock se pierden ticks (lee dirty=True, lo resetea, mientras
            # acá se está escribiendo un precio nuevo).
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
                rows: list[dict] = []
                with self._state_lock:
                    snapshot_items = list(self.state.items())
                for ticker, s in snapshot_items:
                    if not s.get("dirty"):
                        continue
                    row = {"ticker": ticker, "updated_at": ts}
                    if s.get("last_price") is not None:
                        row["last_price"] = s["last_price"]
                    if s.get("closing_price") is not None:
                        row["closing_price"] = s["closing_price"]
                    rows.append(row)
                    s["dirty"] = False
                if rows:
                    # UPSERT por ticker, columnas parciales (no pisa closing si solo cambió last).
                    write_snapshot("portfolio_snapshot", ["ticker"], rows)
            except Exception:
                logger.error("Snapshot loop falló:\n%s", traceback.format_exc())


def _refresh_loop(engine: PortfolioSnapshotEngine, ws: WebSocketManager):
    """Cada REFRESH_INTERVAL_S detecta tickers nuevos y los suscribe.

    Orden estricto:
      1) engine.agregar_tickers(nuevos) → inicializa state local.
      2) ws.agregar_suscripciones(nuevos, entries=...) → pyRofex.
      3) Persiste log a manager.portfolio_snapshot_log.

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
    """Audit en manager.portfolio_snapshot_log (SQL). Si falla, warning y sigue."""
    try:
        append_native("portfolio_snapshot_log", [{
            "ts":          datetime.now(UTC),
            "n_actuales":  n_actuales,
            "n_nuevos":    len(nuevos),
            "n_sin_match": len(sin_match),
            "data":        {"nuevos": nuevos, "sin_match": sin_match[:50]},
        }])
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
        "WS arriba. Universo inicial: %d tickers. Snapshot cada %ss → "
        "valuaciones.portfolio_snapshot. Refresh cada %ss. Sin match en pyRofex: %d.",
        len(validados), SNAPSHOT_INTERVAL_S, REFRESH_INTERVAL_S, len(sin_match),
    )
    _persistir_log(engine, len(validados), sorted(validados), list(sin_match))

    lanzar_hilo_vital(_refresh_loop, "refresh_loop", args=(engine, ws))

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
