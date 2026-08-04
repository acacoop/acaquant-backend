"""Motor de dólares MEP/CCL/canje en tiempo real (WebSocket).

Suscribe los 3 tickers necesarios:
- MERV - XMEV - AL30  - CI  (precio en pesos)
- MERV - XMEV - AL30D - CI  (paridad MEP, USD local)
- MERV - XMEV - AL30C - CI  (paridad Cable, USD exterior)

Calcula en cada snapshot:
- MEP   = AL30_offer / AL30D_bid
- CCL   = AL30_offer / AL30C_bid
- canje = (CCL - MEP) / MEP * 100

Persiste en `valuaciones.dolar_snapshot` (1 fila viva, id='current', reescrita
cada INTERVALO_SNAPSHOT_S). El histórico lo sigue escribiendo engines.dolar_mep
(cron cada 15 min).

El endpoint /api/cotizaciones/mep lee `dolar_snapshot` primero (live), con
fallback a `valuaciones.dolar` (último cierre del cron) si la fila no existe.
Lecturas centralizadas en core/dolar_sql.py.

Esqueleto del proceso (señales / WS / snapshot-loop / run): engines/_motor_base.

Ejecutar:
    python -m engines.dolares
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from core.pg_mirror import write_snapshot
from engines._motor_base import SnapshotEngine, correr_motor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("MotorDolares")

INTERVALO_SNAPSHOT_S = 5

TICKER_AL30  = "MERV - XMEV - AL30 - CI"
TICKER_AL30D = "MERV - XMEV - AL30D - CI"
TICKER_AL30C = "MERV - XMEV - AL30C - CI"
TICKERS = [TICKER_AL30, TICKER_AL30D, TICKER_AL30C]


class DolaresEngine(SnapshotEngine):
    """Mantiene book de los 3 tickers y publica MEP/CCL/canje."""

    INTERVALO_SNAPSHOT_S = INTERVALO_SNAPSHOT_S
    # Este motor no trackea volúmenes (EV/NV) — solo book + OHLC.
    ENTRIES = ("BI", "OF", "LA", "OP", "HI", "LO", "CL")

    def __init__(self):
        # SQL-NATIVE (decomiso Mongo): el snapshot live va a valuaciones.dolar_snapshot
        # (1 fila fija id='current', upsert cada 5s).
        super().__init__(TICKERS)

    def _volcar_snapshot(self):
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

        row = {
            "id":          "current",  # única fila fija
            "ts":          ts,
            "al30_offer":  al30_offer,
            "al30d_bid":   al30d_bid,
            "al30c_bid":   al30c_bid,
            "mep":         mep,
            "ccl":         ccl,
            "canje":       canje,
            "source":      "ws_live",
        }
        write_snapshot("dolar_snapshot", ["id"], [row])

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


def run():
    correr_motor("MotorDolares", DolaresEngine)


if __name__ == "__main__":
    run()
