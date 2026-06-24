"""adr_live.py — pull live USD prices del subyacente de cada CEDEAR → SQL.

Cron `*/15 13-20 * * 1-5` — cada 15 min durante US trading hours.
Llena `mercado.adr_snapshot` (un doc por ticker, upsert) con el último quote de
Finnhub. El service del scanner lo combina con `mercado.precios_acciones` (cierres
EOD) para reflejar precio live + retornos rolling.

Cutover SQL-native 2026-06-24: antes escribía Trading.AdrSnapshot (Mongo) + espejo
SQL; ahora escribe SOLO SQL (`pg_mirror.write_native`). El universo sale de
mercado.cedears (espejo SQL del master). Frescura monitoreada vía Manager.JobRuns
(run_tipo="adr_live"). Sin sync ni Mongo.

Shape del jsonb `data`:
  {ticker, c, pc, o, h, l, t, updated_at}   # c=last, pc=prev close NYSE

Off-market hours: /quote sigue devolviendo el último close → idempotente. Pero
corremos solo en hs operativas para no quemar calls.

Rate limit: Finnhub free tier 60/min, core/finnhub.py limita a 40/min. Para ~190
tickers tarda ~5 min por run. Entra dentro de la ventana de 15 min.

Uso manual:
    python -m jobs.adr_live
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from core import pg_mirror
from core.finnhub import FinnhubError, quote
from core.postgres import get_pool

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _underlyings_activos() -> list[str]:
    """Underlyings (US symbol) únicos de los CEDEARs activos, desde mercado.cedears (SQL)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT upper(COALESCE(underlying, ticker_corto)) "
            "FROM mercado.cedears WHERE activo IS TRUE")
        return sorted(r[0] for r in cur.fetchall())


def run() -> int:
    """Trae el quote live de cada underlying y lo upsertea en mercado.adr_snapshot.
    Returns la cantidad de tickers OK."""
    underlyings = _underlyings_activos()
    logger.info("adr_live — %d underlyings a queryar", len(underlyings))

    now = datetime.now(UTC)
    ok = 0
    fail = 0
    sql_rows: list[dict] = []
    for ticker in underlyings:
        try:
            q = quote(ticker)
        except FinnhubError as e:
            logger.warning("%-6s FAIL %s", ticker, e)
            fail += 1
            continue

        c = q.get("c")
        pc = q.get("pc")
        if c is None or (c == 0 and pc == 0):
            logger.warning("%-6s sin data (c=%s pc=%s)", ticker, c, pc)
            fail += 1
            continue

        doc = {
            "ticker":     ticker,
            "c":          c,
            "pc":         pc,
            "o":          q.get("o"),
            "h":          q.get("h"),
            "l":          q.get("l"),
            "t":          q.get("t"),
            "updated_at": now,
        }
        # SQL-native: passthrough jsonb a mercado.adr_snapshot (upsert por ticker).
        sql_rows.append({"ticker": ticker, "data": pg_mirror.doc_iso(doc), "updated_at": now})
        ok += 1

    pg_mirror.write_native("mercado.adr_snapshot", ["ticker"], sql_rows)
    logger.info("Resumen: %d OK · %d fail", ok, fail)
    return ok


if __name__ == "__main__":
    from core.job_runs import JobRunLogger
    with JobRunLogger("adr_live") as jr:
        n = run()
        jr.set_stat("ok", n)
