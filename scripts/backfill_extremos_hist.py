"""backfill_extremos_hist.py — máximo/mínimo histórico SIN cargar la historia.

Decisión del user (2026-07-11): para el máx/mín histórico de cada papel NO se
backfillean 20 años de velas — se pide la historia 2005→arranque de la serie
a Yahoo, se DESTILA el máximo y el mínimo, y se guardan SOLO los lados que
SUPERAN a los extremos de la serie viva (2024+). Si el extremo del papel ya
está en la serie actual (la mayoría), no se guarda nada — y si había un
registro previo que dejó de superar, se borra. La base queda con ≤1 fila
mínima por papel en mercado.precios_extremos_hist.

Idempotente: re-correrlo actualiza/limpia. Solo lecturas a Yahoo (throttled)
+ upserts mínimos. Re-correr cuando se suman CEDEARs nuevos al universo.

Uso (Droplet):
    python -m scripts.backfill_extremos_hist            # todo el universo
    python -m scripts.backfill_extremos_hist --ticker INTC
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import UTC, datetime

from core.postgres import get_pool
from core.yahoo import YahooError, stock_candle
from jobs.precios_acciones_daily import DESDE_BACKFILL, _underlyings_activos

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DESDE_HIST = datetime(2005, 1, 1, tzinfo=UTC)


def _extremos_serie_viva() -> dict[str, tuple[float | None, float | None]]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, max(high), min(low) FROM mercado.precios_acciones "
            "WHERE high IS NOT NULL AND low IS NOT NULL GROUP BY ticker"
        )
        return {r[0]: (float(r[1]), float(r[2])) for r in cur.fetchall()}


def relevar_ticker(ticker: str, serie: tuple[float | None, float | None]) -> str:
    """Devuelve 'guardado' / 'sin_novedad' (extremos ya en la serie viva) /
    'sin_data' / 'error'."""
    try:
        res = stock_candle(ticker, "D", int(DESDE_HIST.timestamp()),
                           int(DESDE_BACKFILL.timestamp()))
    except YahooError as e:
        logger.warning("%-6s yahoo: %s", ticker, e)
        return "error"
    if res.get("s") != "ok" or not res.get("t"):
        return "sin_data"

    highs = [(h, t) for h, t in zip(res.get("h") or [], res["t"]) if h is not None]
    lows = [(lo, t) for lo, t in zip(res.get("l") or [], res["t"]) if lo is not None]
    if not highs or not lows:
        return "sin_data"
    hist_max, ts_max = max(highs)
    hist_min, ts_min = min(lows)
    serie_max, serie_min = serie

    # Solo se persiste el lado que SUPERA a la serie viva (2024+)
    guardar_max = serie_max is None or hist_max > serie_max
    guardar_min = serie_min is None or hist_min < serie_min

    with get_pool().connection() as conn, conn.cursor() as cur:
        if not guardar_max and not guardar_min:
            cur.execute("DELETE FROM mercado.precios_extremos_hist WHERE ticker = %s",
                        (ticker,))
            return "sin_novedad"
        cur.execute(
            """
            INSERT INTO mercado.precios_extremos_hist
                (ticker, max_high, max_fecha, min_low, min_fecha, desde, hasta, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (ticker) DO UPDATE SET
                max_high = EXCLUDED.max_high, max_fecha = EXCLUDED.max_fecha,
                min_low = EXCLUDED.min_low, min_fecha = EXCLUDED.min_fecha,
                desde = EXCLUDED.desde, hasta = EXCLUDED.hasta, updated_at = now()
            """,
            (
                ticker,
                hist_max if guardar_max else None,
                datetime.fromtimestamp(ts_max, tz=UTC).date() if guardar_max else None,
                hist_min if guardar_min else None,
                datetime.fromtimestamp(ts_min, tz=UTC).date() if guardar_min else None,
                DESDE_HIST.date(), DESDE_BACKFILL.date(),
            ),
        )
    return "guardado"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", help="solo un underlying (debug)")
    args = ap.parse_args()

    tickers = _underlyings_activos(args.ticker)
    serie = _extremos_serie_viva()
    logger.info("extremos históricos %s→%s — %d underlyings",
                DESDE_HIST.date(), DESDE_BACKFILL.date(), len(tickers))

    conteo: dict[str, int] = {}
    for t in tickers:
        resultado = relevar_ticker(t, serie.get(t, (None, None)))
        conteo[resultado] = conteo.get(resultado, 0) + 1
        logger.info("%-6s %s", t, resultado)
        time.sleep(1.0)

    logger.info("Resumen: %s", conteo)
    logger.info("('sin_novedad' = el máx y el mín ya están en la serie 2024+ — no se guardó nada)")


if __name__ == "__main__":
    main()
