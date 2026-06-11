"""Motor de dólares MEP/CCL/canje en tiempo real (WebSocket).

Suscribe los 3 tickers necesarios:
- MERV - XMEV - AL30  - CI  (precio en pesos)
- MERV - XMEV - AL30D - CI  (paridad MEP, USD local)
- MERV - XMEV - AL30C - CI  (paridad Cable, USD exterior)

Calcula en cada snapshot:
- MEP   = AL30_offer / AL30D_bid
- CCL   = AL30_offer / AL30C_bid
- canje = (CCL - MEP) / MEP * 100

Persiste en Valuaciones.DolarSnapshot (1 doc, replaced cada SNAPSHOT_S).
El histórico sigue siendo escrito por engines.dolar_mep (cron cada 15 min).

El endpoint /api/cotizaciones/mep lee de DolarSnapshot primero (live),
con fallback a Valuaciones.Dolar (último cierre del cron) si el snapshot
no existe.

Ejecutar:
    python -m engines.dolares
"""
from __future__ import annotations

import logging
import signal
import threading
import time
import traceback
from datetime import UTC, datetime

from core.mongo import get_mongo_client
from core.rofex_session import inicializar_sesion
from core.threads import lanzar_hilo_vital
from core.websocket import WebSocketManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("MotorDolares")

INTERVALO_SNAPSHOT_S = 5

TICKER_AL30  = "MERV - XMEV - AL30 - CI"
TICKER_AL30D = "MERV - XMEV - AL30D - CI"
TICKER_AL30C = "MERV - XMEV - AL30C - CI"
TICKERS = [TICKER_AL30, TICKER_AL30D, TICKER_AL30C]

_running = True


def _handle_signal(sig, frame):
    global _running
    logger.info("Señal de cierre recibida, apago.")
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────


class DolaresEngine:
    """Mantiene book de los 3 tickers y publica MEP/CCL/canje."""

    def __init__(self):
        self.client = get_mongo_client()
        self.col_snap = self.client["Valuaciones"]["DolarSnapshot"]

        # market_state[ticker] = {bid: {price,size}, offer: {price,size}, last:..}
        self.market_state: dict[str, dict] = {t: {} for t in TICKERS}
        self._state_lock = threading.Lock()

        lanzar_hilo_vital(self._snapshot_loop, "snapshot_loop")

    # ─── WS handler ───────────────────────────────────────────────────────
    def update_price(self, ticker: str, data: dict):
        with self._state_lock:
            if ticker not in self.market_state:
                return
            st = self.market_state[ticker]
            if "BI" in data:
                st["bid"] = data["BI"][0] if data["BI"] else None
            if "OF" in data:
                st["offer"] = data["OF"][0] if data["OF"] else None
            if "LA" in data:
                st["last"] = data["LA"]
            if "OP" in data and data["OP"] is not None:
                st["open"] = data["OP"]
            if "HI" in data and data["HI"] is not None:
                st["high"] = data["HI"]
            if "LO" in data and data["LO"] is not None:
                st["low"] = data["LO"]
            if "CL" in data:
                st["closing"] = data["CL"]

    # ─── Snapshot loop ────────────────────────────────────────────────────
    def _snapshot_loop(self):
        while _running:
            time.sleep(INTERVALO_SNAPSHOT_S)
            try:
                self._volcar()
            except Exception:
                logger.error("snapshot_loop error:\n%s", traceback.format_exc())

    def _volcar(self):
        ts = datetime.now(UTC)
        with self._state_lock:
            al30  = self.market_state[TICKER_AL30]
            al30d = self.market_state[TICKER_AL30D]
            al30c = self.market_state[TICKER_AL30C]

            al30_offer  = self._extract_price(al30.get("offer"))
            al30d_bid   = self._extract_price(al30d.get("bid"))
            al30c_bid   = self._extract_price(al30c.get("bid"))

        mep = self._safe_div(al30_offer, al30d_bid, 4)
        ccl = self._safe_div(al30_offer, al30c_bid, 4)
        canje = None
        if mep and ccl:
            canje = round((ccl - mep) / mep * 100, 2)

        # Si los 3 inputs son None, no escribimos para no pisar el snapshot viejo.
        if al30_offer is None and al30d_bid is None and al30c_bid is None:
            return

        doc = {
            "_id":         "current",  # único doc fijo
            "timestamp":   ts,
            "al30_offer":  al30_offer,
            "al30d_bid":   al30d_bid,
            "al30c_bid":   al30c_bid,
            "mep":         mep,
            "ccl":         ccl,
            "canje":       canje,
            "source":      "ws_live",
        }
        self.col_snap.replace_one({"_id": "current"}, doc, upsert=True)

    @staticmethod
    def _extract_price(level: dict | None) -> float | None:
        if not level or not isinstance(level, dict):
            return None
        p = level.get("price")
        try:
            return float(p) if p is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_div(a: float | None, b: float | None, prec: int) -> float | None:
        if a is None or b is None or b <= 0 or a <= 0:
            return None
        try:
            return round(a / b, prec)
        except (TypeError, ValueError, ZeroDivisionError):
            return None


# ─────────────────────────────────────────────────────────────────────────────
# Bucle principal
# ─────────────────────────────────────────────────────────────────────────────


def run():
    logger.info("Motor Dólares iniciando...")
    if not inicializar_sesion():
        return

    engine = DolaresEngine()
    ws = WebSocketManager(engine)

    if not ws.iniciar_ws(TICKERS, depth=1):
        logger.error("No pude iniciar WS")
        return

    logger.info(
        "WS arriba. Suscripto a %d tickers. Snapshot cada %ds → Valuaciones.DolarSnapshot",
        len(TICKERS), INTERVALO_SNAPSHOT_S,
    )

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
