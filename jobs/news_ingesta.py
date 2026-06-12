"""news_ingesta.py — Ingesta de RSS de medios económicos argentinos.

Corre cada 15 min vía cron y mete las headlines en `News.Headlines` con dedup
por URL. Si algún feed devuelve error, se loggea y se sigue con el resto.

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

from pymongo.errors import DuplicateKeyError

from core.mongo import get_mongo_client
from core.pg_mirror import doc_iso, jobs_on, mirror_job, prune_job

logger = logging.getLogger(__name__)

# Retención: las noticias NO se acumulan — viven 2 días y Mongo las borra solo
# (TTL index sobre fecha_publicacion). El espejo SQL aplica la misma retención
# vía prune_job tras cada ingesta (y el delete-orphans de sync_postgres de red).
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
    # Feeds pendientes de corregir URL:
    # - Cronista finanzas-mercados (XML syntax error al día de hoy)
    # - BAE economía (XML mismatched tag)
]


def _ensure_indexes(coll) -> None:
    coll.create_index("url", unique=True)
    coll.create_index([("fecha_publicacion", -1)])
    coll.create_index([("fuente", 1), ("fecha_publicacion", -1)])
    coll.create_index("categoria")
    # TTL: Mongo borra solo los docs con fecha_publicacion > RETENCION_DIAS días.
    # Clave ascendente {f:1} ≠ {f:-1} de arriba → conviven sin conflicto.
    coll.create_index(
        [("fecha_publicacion", 1)],
        name="ttl_fecha_publicacion",
        expireAfterSeconds=RETENCION_DIAS * 86400,
    )


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


def ingesta_una_fuente(feed_cfg: dict, coll) -> tuple[int, int, int]:
    """Parsea un feed y hace upsert en la colección. Devuelve (insertados, duplicados, errores)."""
    import feedparser
    insertados = duplicados = errores = 0
    try:
        parsed = feedparser.parse(feed_cfg["url"])
    except Exception as e:
        logger.warning("feed %s fallo al parsear: %s", feed_cfg["url"], e)
        return 0, 0, 1

    if parsed.bozo and not parsed.entries:
        logger.warning("feed %s no devolvió entries (bozo=%s): %s",
                       feed_cfg["url"], parsed.bozo, getattr(parsed, "bozo_exception", ""))
        return 0, 0, 1

    now = datetime.now(UTC)
    for entry in parsed.entries:
        url = getattr(entry, "link", "") or ""
        titulo = getattr(entry, "title", "") or ""
        if not url or not titulo:
            continue

        doc = {
            "url":                url,
            "fuente":             feed_cfg["fuente"],
            "categoria":          feed_cfg["categoria"],
            "titulo":             titulo.strip(),
            "excerpt":            _clean_excerpt(getattr(entry, "summary", "") or ""),
            "fecha_publicacion":  _parse_entry_date(entry),
            "fetched_at":         now,
        }
        try:
            coll.insert_one(doc)
            insertados += 1
        except DuplicateKeyError:
            duplicados += 1
        except Exception as e:
            logger.exception("error insertando %s: %s", url, e)
            errores += 1

    return insertados, duplicados, errores


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

    client = get_mongo_client()
    coll = client["News"]["Headlines"]
    _ensure_indexes(coll)

    t0 = time.time()
    total_ins = total_dup = total_err = 0
    for f in feeds:
        ins, dup, err = ingesta_una_fuente(f, coll)
        total_ins += ins
        total_dup += dup
        total_err += err
        logger.info("[%s/%s] ins=%d dup=%d err=%d",
                    f["fuente"], f["categoria"], ins, dup, err)

    # Dual-write a Postgres (flag MERCADO_SQL_WRITE, best-effort): espejo del
    # estado completo (chico — la TTL mantiene ≤2 días) + misma retención en PG.
    if jobs_on():
        rows = [{"url": d["url"], "fecha_publicacion": d.get("fecha_publicacion"),
                 "fuente": d.get("fuente"), "categoria": d.get("categoria"),
                 "titulo": d.get("titulo"), "data": doc_iso(d)}
                for d in coll.find({}, {"_id": 0}) if d.get("url")]
        mirror_job("news_headlines", ["url"], rows)
        prune_job("news_headlines", "fecha_publicacion", RETENCION_DIAS)

    elapsed = time.time() - t0
    logger.info("DONE — feeds=%d ins=%d dup=%d err=%d %.1fs",
                len(feeds), total_ins, total_dup, total_err, elapsed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
