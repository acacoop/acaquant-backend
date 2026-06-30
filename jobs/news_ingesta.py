"""news_ingesta.py — Ingesta de RSS de medios económicos argentinos.

Corre cada 15 min vía cron y escribe las headlines DIRECTO a SQL `news_headlines`
(upsert por URL + prune de retención). SQL-native: ya NO escribe Mongo. Si un feed
devuelve error, se loggea y se sigue con el resto.

Uso:
    python -m jobs.news_ingesta           # una corrida normal
    python -m jobs.news_ingesta --backfill # ignora fetched_at reciente, relee todo

Agregar una fuente nueva: sumar un dict a NEWS_FEEDS más abajo.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime

from core.pg_mirror import doc_iso, prune_native, write_native

logger = logging.getLogger(__name__)

# Retención: las noticias NO se acumulan — viven 2 días. Tras cada ingesta se
# borran las filas más viejas que RETENCION_DIAS con `prune_native` (SQL-native).
RETENCION_DIAS = 2

# Fuentes con RSS público. Si una URL cambia, solo se ignora esa fuente esa
# corrida (el resto sigue). El campo `fuente` se usa también como clave
# visible en la UI.
NEWS_FEEDS: list[dict[str, str]] = [
    # Ámbito
    {"fuente": "Ámbito",       "categoria": "economia",  "url": "https://www.ambito.com/rss/economia.xml"},
    {"fuente": "Ámbito",       "categoria": "finanzas",  "url": "https://www.ambito.com/rss/finanzas.xml"},
    {"fuente": "Ámbito",       "categoria": "mercados",  "url": "https://www.ambito.com/rss/negocios.xml"},

    # Cronista (finanzas-mercados rompe XML, economía OK)
    {"fuente": "Cronista",     "categoria": "economia",  "url": "https://www.cronista.com/files/rss/economia-politica.xml"},

    # iProfesional
    {"fuente": "iProfesional", "categoria": "finanzas",  "url": "https://www.iprofesional.com/rss/finanzas"},
    {"fuente": "iProfesional", "categoria": "economia",  "url": "https://www.iprofesional.com/rss/economia"},

    # Clarín
    {"fuente": "Clarín",       "categoria": "economia",  "url": "https://www.clarin.com/rss/economia/"},

    # Infobae (URL vía Arc XP — formato path-based, filtra correctamente)
    {"fuente": "Infobae",      "categoria": "economia",  "url": "https://www.infobae.com/arc/outboundfeeds/rss/category/economia/?outputType=xml"},

    # La Nación (URL vía Arc XP)
    {"fuente": "La Nación",    "categoria": "economia",  "url": "https://www.lanacion.com.ar/arc/outboundfeeds/rss/category/economia/?outputType=xml"},

    # ───────────────────────────────────────────────────────────────────────
    # MUNDO — renta variable internacional (Wall Street: SP/Nasdaq, earnings,
    # movers, tasas). categoria="mundo" → botón dedicado en el panel. Verificados
    # vivos 2026-06-25 (WSJ/Yahoo/Investing/Fed); CNBC y MarketWatch a confirmar
    # con feedparser en el Droplet (si 403/dead, el job los saltea sin romper).
    {"fuente": "WSJ",          "categoria": "mundo", "url": "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"},
    {"fuente": "Yahoo Finance","categoria": "mundo", "url": "https://finance.yahoo.com/news/rssindex"},
    {"fuente": "Investing",    "categoria": "mundo", "url": "https://www.investing.com/rss/news_25.rss"},
    {"fuente": "Fed",          "categoria": "mundo", "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
    {"fuente": "CNBC",         "categoria": "mundo", "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html"},
    {"fuente": "MarketWatch",  "categoria": "mundo", "url": "https://www.marketwatch.com/rss/topstories"},

    # ───────────────────────────────────────────────────────────────────────
    # Feeds pendientes de corregir URL:
    # - Cronista finanzas-mercados (XML syntax error al día de hoy)
    # - BAE economía (XML mismatched tag)
]


def _parse_entry_date(entry) -> datetime:
    """Intenta extraer fecha del entry del feed; fallback = ahora UTC."""
    try:
        import feedparser  # noqa: F401 (importado acá solo para tipear entry)
    except ImportError:
        pass
    # feedparser expone struct_time en `published_parsed` o `updated_parsed`
    for key in ("published_parsed", "updated_parsed"):
        t = getattr(entry, key, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=UTC)
            except Exception:
                continue
    return datetime.now(UTC)


def _clean_excerpt(raw: str, maxlen: int = 400) -> str:
    if not raw:
        return ""
    # Limpiar tags HTML crudos con un pase regex (simple, no queremos BeautifulSoup).
    import re
    txt = re.sub(r"<[^>]+>", "", raw)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:maxlen]


def ingesta_una_fuente(feed_cfg: dict) -> list[dict]:
    """Parsea un feed y devuelve los docs de headlines (SQL-native: NO escribe Mongo)."""
    import feedparser
    try:
        parsed = feedparser.parse(feed_cfg["url"])
    except Exception as e:
        logger.warning("feed %s fallo al parsear: %s", feed_cfg["url"], e)
        return []

    if parsed.bozo and not parsed.entries:
        logger.warning("feed %s no devolvió entries (bozo=%s): %s",
                       feed_cfg["url"], parsed.bozo, getattr(parsed, "bozo_exception", ""))
        return []

    now = datetime.now(UTC)
    docs: list[dict] = []
    for entry in parsed.entries:
        url = getattr(entry, "link", "") or ""
        titulo = getattr(entry, "title", "") or ""
        if not url or not titulo:
            continue
        docs.append({
            "url":                url,
            "fuente":             feed_cfg["fuente"],
            "categoria":          feed_cfg["categoria"],
            "titulo":             titulo.strip(),
            "excerpt":            _clean_excerpt(getattr(entry, "summary", "") or ""),
            "fecha_publicacion":  _parse_entry_date(entry),
            "fetched_at":         now,
        })
    return docs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true", help="(reservado)")
    parser.add_argument("--fuente", default=None, help="Correr solo una fuente (ej: 'Ámbito')")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    feeds = NEWS_FEEDS
    if args.fuente:
        feeds = [f for f in feeds if f["fuente"].lower() == args.fuente.lower()]
        if not feeds:
            logger.error("fuente %s no encontrada", args.fuente)
            return 2

    t0 = time.time()
    docs: list[dict] = []
    for f in feeds:
        d = ingesta_una_fuente(f)
        docs.extend(d)
        logger.info("[%s/%s] %d headlines", f["fuente"], f["categoria"], len(d))

    # SQL-NATIVE: upsert directo a news_headlines (sin Mongo). Dedup por url + retención.
    por_url = {d["url"]: d for d in docs if d.get("url")}
    rows = [{"url": d["url"], "fecha_publicacion": d.get("fecha_publicacion"),
             "fuente": d.get("fuente"), "categoria": d.get("categoria"),
             "titulo": d.get("titulo"), "data": doc_iso(d)}
            for d in por_url.values()]
    n = write_native("news_headlines", ["url"], rows)
    prune_native("news_headlines", "fecha_publicacion", RETENCION_DIAS)

    elapsed = time.time() - t0
    logger.info("DONE — feeds=%d headlines=%d upserted=%d %.1fs",
                len(feeds), len(rows), n, elapsed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
