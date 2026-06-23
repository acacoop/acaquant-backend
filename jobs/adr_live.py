"""adr_live.py — pull live USD prices del subyacente de cada CEDEAR.

Cron `*/15 13-20 * * 1-5` — cada 15 min durante US trading hours.
Llena `Trading.AdrSnapshot` (un doc por ticker, upsert) con el último
quote de Finnhub. El service del scanner lo combina con
`Trading.PreciosAcciones` (cierres EOD para los anchors 7d/mtd/ytd)
para que la vista ADR refleje el precio live + retornos rolling.

Shape del doc:
  {
    ticker:     "NVDA",
    c:          227.27,   # current/last
    pc:         220.78,   # previous close (NYSE EOD)
    o:          224.80,   # open hoy
    h:          227.84,
    l:          221.57,
    t:          1747136680,  # Finnhub timestamp (unix s)
    updated_at: ISODate,
  }

Off-market hours: /quote sigue devolviendo el último close → idempotente,
nada raro. Pero corremos solo en hs operativas para no quemar calls.

Rate limit: Finnhub free tier 60/min, core/finnhub.py limita a 40/min
internamente. Para 71 tickers tarda ~2 min por run. Sobra dentro de la
ventana de 15 min.

Uso manual:
    python -m jobs.adr_live
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from core import pg_mirror
from core.finnhub import FinnhubError, quote
from core.mongo import get_mongo_client

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def _underlyings_activos() -> list[str]:
    """Underlyings (US symbol) únicos de los CEDEARs activos."""
    db = get_mongo_client()["Trading"]
    out: set[str] = set()
    for d in db["Cedears"].find(
        {"activo": True}, {"_id": 0, "ticker_corto": 1, "underlying": 1}
    ):
        out.add(d.get("underlying") or d["ticker_corto"])
    return sorted(out)


def run() -> None:
    underlyings = _underlyings_activos()
    logger.info("adr_live — %d underlyings a queryar", len(underlyings))

    client = get_mongo_client()
    col = client["Trading"]["AdrSnapshot"]
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
        col.update_one({"ticker": ticker}, {"$set": doc}, upsert=True)
        # Espejo SQL (flag SNAPSHOT_SQL, best-effort): passthrough jsonb a
        # mercado.adr_snapshot. doc_iso convierte el datetime aware a ISO.
        sql_rows.append({"ticker": ticker, "data": pg_mirror.doc_iso(doc), "updated_at": now})
        ok += 1

    pg_mirror.mirror_snapshot("mercado.adr_snapshot", ["ticker"], sql_rows)

    logger.info("Resumen: %d OK · %d fail", ok, fail)


if __name__ == "__main__":
    run()
