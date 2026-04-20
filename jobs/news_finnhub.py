"""news_finnhub.py — ingesta de noticias desde Finnhub.

Tres pipes:
1. /news?category=general → news globales. Fuente='Finnhub' cat='global'.
2. /news?category=forex   → news FX.       Fuente='Finnhub' cat='forex'.
3. /news?category=merger  → M&A.           Fuente='Finnhub' cat='mercados'.
4. /company-news por ADR argentino/LATAM (GGAL, YPF, BMA, etc). Fuente='Finnhub',
   cat='adr' + guardamos symbol como tag.

Todo va a News.Headlines con dedup por URL (mismo index que news_ingesta.py).

Cron sugerido: cada 30 min en horario de mercado US:
    */30 12-23 * * 1-5 cd /root/TradingAV && venv/bin/python -m jobs.news_finnhub
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

from pymongo.errors import DuplicateKeyError

from core.finnhub import FinnhubError, company_news, general_news
from core.mongo import get_mongo_client

logger = logging.getLogger(__name__)

# Categorías generales
CATEGORIAS: list[tuple[str, str]] = [
    # (categoria_nuestra, fh_category)
    ("global",   "general"),
    ("forex",    "forex"),
    ("mercados", "merger"),
]

# ADRs AR + LATAM (core para la mesa)
ADR_TICKERS = [
    "GGAL", "YPF", "BMA", "BBAR", "TGS", "PAM", "LOMA",
    "CRESY", "IRS", "EDN", "CEPU", "TEO", "SUPV", "VIST",
    # LATAM benchmarks
    "VALE", "ITUB", "PBR", "AMX",
]


def _to_doc(item: dict, categoria: str, extra_tag: str | None = None) -> dict | None:
    url = (item.get("url") or "").strip()
    headline = (item.get("headline") or "").strip()
    if not url or not headline:
        return None

    ts = item.get("datetime")
    if isinstance(ts, (int, float)) and ts > 0:
        fecha = datetime.fromtimestamp(ts, tz=timezone.utc)
    else:
        fecha = datetime.now(timezone.utc)

    doc = {
        "url": url,
        "fuente": "Finnhub",
        "categoria": categoria,
        "titulo": headline,
        "excerpt": (item.get("summary") or "").strip()[:500],
        "fecha_publicacion": fecha,
        "fetched_at": datetime.now(timezone.utc),
    }
    if extra_tag:
        doc["tag"] = extra_tag  # ticker ADR, por ejemplo
    return doc


def ingesta(categorias: bool = True, adrs: bool = True) -> int:
    client = get_mongo_client()
    coll = client["News"]["Headlines"]
    coll.create_index("url", unique=True)

    ins = dup = err = 0

    if categorias:
        for cat_nuestra, fh_cat in CATEGORIAS:
            try:
                items = general_news(fh_cat)
            except FinnhubError as e:
                logger.warning("general_news %s failed: %s", fh_cat, e)
                err += 1
                continue
            for it in items:
                doc = _to_doc(it, cat_nuestra)
                if not doc:
                    continue
                try:
                    coll.insert_one(doc)
                    ins += 1
                except DuplicateKeyError:
                    dup += 1
            logger.info("general/%s: %d items", fh_cat, len(items))

    if adrs:
        hasta = datetime.now(timezone.utc).date()
        desde = hasta - timedelta(days=3)
        for sym in ADR_TICKERS:
            try:
                items = company_news(sym, desde.isoformat(), hasta.isoformat())
            except FinnhubError as e:
                logger.warning("company_news %s failed: %s", sym, e)
                err += 1
                continue
            for it in items:
                doc = _to_doc(it, "adr", extra_tag=sym)
                if not doc:
                    continue
                try:
                    coll.insert_one(doc)
                    ins += 1
                except DuplicateKeyError:
                    dup += 1

    logger.info("news_finnhub — ins=%d dup=%d err=%d", ins, dup, err)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--solo-adrs", action="store_true")
    parser.add_argument("--solo-categorias", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    return ingesta(
        categorias=not args.solo_adrs,
        adrs=not args.solo_categorias,
    )


if __name__ == "__main__":
    sys.exit(main())
