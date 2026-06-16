"""news_finnhub.py — ingesta de noticias desde Finnhub.

Tres pipes:
1. /news?category=general → news globales. Fuente='Finnhub' cat='global'.
2. /news?category=forex   → news FX.       Fuente='Finnhub' cat='forex'.
3. /news?category=merger  → M&A.           Fuente='Finnhub' cat='mercados'.
4. /company-news por ADR argentino/LATAM (GGAL, YPF, BMA, etc). Fuente='Finnhub',
   cat='adr' + guardamos symbol como tag.

Todo va DIRECTO a SQL `news_headlines` (upsert por URL), SQL-native como news_ingesta.py.

Cron sugerido: cada 30 min en horario de mercado US:
    */30 12-23 * * 1-5 cd /root/TradingAV && venv/bin/python -m jobs.news_finnhub
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta

from core.finnhub import FinnhubError, company_news, general_news
from core.pg_mirror import doc_iso, write_native

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
        fecha = datetime.fromtimestamp(ts, tz=UTC)
    else:
        fecha = datetime.now(UTC)

    doc = {
        "url": url,
        "fuente": "Finnhub",
        "categoria": categoria,
        "titulo": headline,
        "excerpt": (item.get("summary") or "").strip()[:500],
        "fecha_publicacion": fecha,
        "fetched_at": datetime.now(UTC),
    }
    if extra_tag:
        doc["tag"] = extra_tag  # ticker ADR, por ejemplo
    return doc


def ingesta(categorias: bool = True, adrs: bool = True) -> int:
    docs: list[dict] = []
    err = 0

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
                if doc:
                    docs.append(doc)
            logger.info("general/%s: %d items", fh_cat, len(items))

    if adrs:
        hasta = datetime.now(UTC).date()
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
                if doc:
                    docs.append(doc)

    # SQL-NATIVE: upsert directo a news_headlines (sin Mongo). Dedup por url.
    # La retención la aplica news_ingesta (prune_native sobre la misma tabla).
    por_url = {d["url"]: d for d in docs if d.get("url")}
    rows = [{"url": d["url"], "fecha_publicacion": d.get("fecha_publicacion"),
             "fuente": d.get("fuente"), "categoria": d.get("categoria"),
             "titulo": d.get("titulo"), "data": doc_iso(d)}
            for d in por_url.values()]
    n = write_native("news_headlines", ["url"], rows)
    logger.info("news_finnhub — headlines=%d upserted=%d err=%d", len(rows), n, err)
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
