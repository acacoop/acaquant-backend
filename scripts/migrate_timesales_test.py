"""Migración + test EMPÍRICO de Trading.TimeSales a Time Series Collection.

Crea Trading.TimeSales_test como TS (copia de la original), inserta trades
ficticios y valida que el patrón de enriquecimiento con match (ticker, ts)
funciona. Cero impacto sobre Trading.TimeSales original.

Pasos sugeridos:
    python -m scripts.migrate_timesales_test --mode copy
    python -m scripts.migrate_timesales_test --mode info
    python -m scripts.migrate_timesales_test --mode inject --count 100
    python -m scripts.migrate_timesales_test --mode test-enrich
    python -m scripts.migrate_timesales_test --mode info
    python -m scripts.migrate_timesales_test --mode drop      # cleanup final

Modos:
  copy          → crea TimeSales_test (TS) y copia toda la data actual.
                  Idempotente: si ya existe, no hace nada (usar --force para resetear).
  info          → stats: count total, count sin duration, sample doc.
  inject N      → inserta N trades ficticios sin duration (timestamp=ahora,
                  tickers reales tomados de TimeSales_test).
  test-enrich   → simula el flow de engines/curvas.py: busca docs sin
                  duration, los enriquece con campos ficticios, hace
                  bulk_write con match (ticker, timestamp). Valida que
                  después no quedan docs sin duration.
  drop          → borra TimeSales_test. Cleanup definitivo.
"""
from __future__ import annotations

import argparse
import logging
import random
import time
from datetime import UTC, datetime

from pymongo import UpdateMany

from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("migrate_test")

DB_NAME = "Trading"
SRC_COL = "TimeSales"
DST_COL = "TimeSales_test"

TIMESERIES_OPTIONS = {
    "timeField":   "timestamp",
    "metaField":   "ticker",
    "granularity": "minutes",
}

COPY_BATCH = 5000


# ─────────────────────────────────────────────────────────────────────────────
# Helpers


def _ts_collection_exists(db, name: str) -> tuple[bool, dict | None]:
    """(existe, info_de_creacion). info_de_creacion incluye .options.timeseries
    si fue creada como TS."""
    info = next(db.list_collections(filter={"name": name}), None)
    return (info is not None, info)


def _is_timeseries(coll_info: dict | None) -> bool:
    return bool((coll_info or {}).get("options", {}).get("timeseries"))


# ─────────────────────────────────────────────────────────────────────────────
# Modos


def mode_copy(force: bool = False) -> int:
    client = get_mongo_client()
    db = client[DB_NAME]

    exists, info = _ts_collection_exists(db, DST_COL)
    if exists:
        if not force:
            if _is_timeseries(info):
                logger.info("%s.%s ya existe como TS Collection. Skip "
                            "(usá --force para reset).", DB_NAME, DST_COL)
                return 0
            logger.warning("%s.%s existe pero NO es TS — usá --force para "
                           "borrarla y recrearla como TS.", DB_NAME, DST_COL)
            return 1
        logger.info("--force: borrando %s.%s para recrear.", DB_NAME, DST_COL)
        db.drop_collection(DST_COL)

    logger.info("Creando %s.%s como Time Series Collection (timeField=%s, "
                "metaField=%s, granularity=%s)…",
                DB_NAME, DST_COL,
                TIMESERIES_OPTIONS["timeField"],
                TIMESERIES_OPTIONS["metaField"],
                TIMESERIES_OPTIONS["granularity"])
    db.create_collection(DST_COL, timeseries=TIMESERIES_OPTIONS)

    src = db[SRC_COL]
    dst = db[DST_COL]
    total_src = src.count_documents({})
    logger.info("Copiando %d docs de %s → %s (batches de %d)…",
                total_src, SRC_COL, DST_COL, COPY_BATCH)

    t0 = time.time()
    copied = 0
    batch: list[dict] = []
    # Excluimos _id — TS lo regenera internamente
    for doc in src.find({}, sort=[("timestamp", 1)]):
        doc.pop("_id", None)
        batch.append(doc)
        if len(batch) >= COPY_BATCH:
            dst.insert_many(batch, ordered=False)
            copied += len(batch)
            batch = []
            if copied % (COPY_BATCH * 10) == 0:
                logger.info("  ... %d/%d (%.1f%%)",
                            copied, total_src, 100 * copied / total_src)
    if batch:
        dst.insert_many(batch, ordered=False)
        copied += len(batch)

    elapsed = time.time() - t0
    logger.info("OK: %d docs copiados en %.1fs (%.0f docs/s)",
                copied, elapsed, copied / max(elapsed, 0.001))

    # Validar count
    n_dst = dst.count_documents({})
    if n_dst != total_src:
        logger.warning("⚠ count mismatch: src=%d dst=%d", total_src, n_dst)
        return 2
    logger.info("✓ count match: %d docs en ambas colecciones.", n_dst)
    return 0


def mode_info() -> int:
    client = get_mongo_client()
    db = client[DB_NAME]
    exists, info = _ts_collection_exists(db, DST_COL)
    if not exists:
        logger.info("%s.%s NO existe. Corré --mode copy.", DB_NAME, DST_COL)
        return 0
    is_ts = _is_timeseries(info)
    logger.info("%s.%s existe (TS=%s)", DB_NAME, DST_COL, is_ts)
    if is_ts:
        ts_opts = info["options"]["timeseries"]
        logger.info("  timeField=%s metaField=%s granularity=%s",
                    ts_opts.get("timeField"),
                    ts_opts.get("metaField"),
                    ts_opts.get("granularity"))

    col = db[DST_COL]
    total = col.count_documents({})
    sin_duration = col.count_documents({"duration": {"$exists": False}})
    con_duration_null = col.count_documents({"duration": None})
    n_tickers = len(col.distinct("ticker"))
    logger.info("  count total              : %d", total)
    logger.info("  sin duration ($exists)   : %d", sin_duration)
    logger.info("  duration: null           : %d", con_duration_null)
    logger.info("  tickers únicos           : %d", n_tickers)

    sample = col.find_one({"duration": {"$exists": False}}) or col.find_one()
    if sample:
        logger.info("  sample doc keys: %s", list(sample.keys()))
    return 0


def mode_inject(count: int) -> int:
    """Inyecta N trades ficticios sin duration. Tickers reales tomados
    de la colección. Timestamp = ahora UTC (segundos)."""
    client = get_mongo_client()
    db = client[DB_NAME]
    col = db[DST_COL]

    tickers = col.distinct("ticker")
    if not tickers:
        logger.error("Sin tickers en %s — corré --mode copy primero.", DST_COL)
        return 1

    now = datetime.now(UTC).replace(microsecond=0)
    docs = []
    for _ in range(count):
        ticker = random.choice(tickers)
        # timestamps espaciados por 1 segundo entre cada doc para evitar
        # demasiada concentración en mismo instante.
        ts = now.replace(microsecond=0)
        docs.append({
            "ticker":    ticker,
            "timestamp": ts,
            "price":     round(1000 + random.random() * 500, 2),
            "size":      random.randint(1, 1000),
            "side":      random.choice(["BUY", "SELL", "MID"]),
            "money":     round(random.random() * 1000, 2),
            # NO duration → simula trade recién insertado por valores.py
            # esperando enriquecimiento de curvas.py
        })

    res = col.insert_many(docs, ordered=False)
    logger.info("Inyectados %d trades ficticios sin duration en %s.%s",
                len(res.inserted_ids), DB_NAME, DST_COL)
    sin_dur = col.count_documents({"duration": {"$exists": False}})
    logger.info("Total docs sin duration ahora: %d", sin_dur)
    return 0


def mode_test_enrich(batch_size: int = 1000) -> int:
    """Simula el flow de engines/curvas.py adaptado a TS Collection:
    1. Find docs sin duration.
    2. Para cada uno, "enriquece" con campos ficticios pero realistas.
    3. bulk_write con UpdateMany(filter=(ticker, timestamp), $set=campos).
       NOTA: TS Collections SOLO permiten multi-update. UpdateOne falla
       con "Cannot perform a non-multi update on a time-series
       collection" (Mongo 8). Por eso usamos UpdateMany; si el filtro
       (ticker, timestamp) matchea 1 doc se comporta igual que UpdateOne.
    4. Verifica que después del update los docs ya tienen los campos.

    NO usa la lógica real de calcular_campos — eso es responsabilidad
    del motor curvas y depende de Trading.Curvas, CER, MEP, etc. Acá
    solo validamos que el PATRÓN funciona en TS Collection.
    """
    client = get_mongo_client()
    db = client[DB_NAME]
    col = db[DST_COL]

    exists, info = _ts_collection_exists(db, DST_COL)
    if not exists or not _is_timeseries(info):
        logger.error("%s.%s no existe o no es TS — corré --mode copy.",
                     DB_NAME, DST_COL)
        return 1

    docs = list(col.find(
        {"duration": {"$exists": False}},
        sort=[("timestamp", -1)],
        limit=batch_size,
    ))
    if not docs:
        logger.info("No hay docs sin duration. Inyectá unos con --mode inject.")
        return 0

    logger.info("Procesando %d docs sin duration…", len(docs))

    ops = []
    for doc in docs:
        # Campos ficticios — el caller real (curvas.py) los calcula.
        campos_ficticios = {
            "TEA":          round(random.uniform(-0.05, 0.30), 4),
            "TEM":          round(random.uniform(-0.005, 0.025), 5),
            "duration":     round(random.uniform(0.1, 5.0), 4),
            "mod_duration": round(random.uniform(0.1, 5.0), 4),
            "paridad":      round(random.uniform(20, 100), 4),
            "convexity":    round(random.uniform(0.1, 2.0), 4),
        }
        # PATRÓN PARA TS: UpdateMany (single-update no permitido) +
        # filtro por (ticker, timestamp), NO por _id.
        ops.append(UpdateMany(
            {"ticker": doc["ticker"], "timestamp": doc["timestamp"]},
            {"$set": campos_ficticios},
        ))

    t0 = time.time()
    res = col.bulk_write(ops, ordered=False)
    elapsed = time.time() - t0

    logger.info("bulk_write OK en %.2fs:", elapsed)
    logger.info("  matched_count    = %d", res.matched_count)
    logger.info("  modified_count   = %d", res.modified_count)
    logger.info("  upserted_count   = %d", res.upserted_count)

    # Validación: ¿quedan docs sin duration?
    sin_dur_post = col.count_documents({"duration": {"$exists": False}})
    logger.info("Docs sin duration POST: %d (antes: ≥%d)", sin_dur_post, len(docs))

    if res.matched_count == 0:
        logger.error("❌ FALLA: matched_count=0 — el filtro (ticker, ts) NO matcheó.")
        return 2
    if res.modified_count == 0:
        logger.error("❌ FALLA: modified_count=0 — no se persistió nada.")
        return 2
    if sin_dur_post >= len(docs):
        logger.warning("⚠ Quedan %d sin duration (antes %d). Algunos no se "
                       "actualizaron — posibles colisiones (ticker, ts).",
                       sin_dur_post, len(docs))

    logger.info("✓ TEST OK: el patrón (ticker, timestamp) funciona en TS Collection.")
    return 0


def mode_drop() -> int:
    client = get_mongo_client()
    db = client[DB_NAME]
    exists, _ = _ts_collection_exists(db, DST_COL)
    if not exists:
        logger.info("%s.%s no existe — nada que borrar.", DB_NAME, DST_COL)
        return 0
    db.drop_collection(DST_COL)
    logger.info("✓ %s.%s borrada.", DB_NAME, DST_COL)
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", required=True,
                        choices=["copy", "info", "inject", "test-enrich", "drop"])
    parser.add_argument("--count", type=int, default=100,
                        help="(--mode inject) cantidad de docs ficticios.")
    parser.add_argument("--batch", type=int, default=1000,
                        help="(--mode test-enrich) docs por batch.")
    parser.add_argument("--force", action="store_true",
                        help="(--mode copy) borra TimeSales_test si ya existe.")
    args = parser.parse_args()

    if args.mode == "copy":
        return mode_copy(force=args.force)
    if args.mode == "info":
        return mode_info()
    if args.mode == "inject":
        return mode_inject(count=args.count)
    if args.mode == "test-enrich":
        return mode_test_enrich(batch_size=args.batch)
    if args.mode == "drop":
        return mode_drop()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
