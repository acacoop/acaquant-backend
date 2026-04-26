"""backfill_mod_duration.py — agregar `mod_duration` a docs históricos.

mod_duration = duration / (1 + TEA), ambos campos ya existen en el doc.
Por eso lo resolvemos con una sola `update_many` con aggregation pipeline:
sin cursor, sin Python en el medio, atomic en Mongo. Segundos en vez de
minutos para 200K+ docs.

Cubre dos colecciones:
  - Trading.TimeSales         (1 doc por trade)
  - Trading.MarketSnapshot    (1 doc por ticker, último estado)

Idempotente: filtra `mod_duration: {$exists: false}`.

Uso:
    python -m scripts.backfill_mod_duration              # corre todo
    python -m scripts.backfill_mod_duration --dry        # solo cuenta
"""
from __future__ import annotations

import argparse
import logging

from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("backfill_mod_duration")


# Pipeline de cálculo: D / (1 + TEA), redondeado a 4 decimales.
# Lo aplicamos en TimeSales y en MarketSnapshot.metrics.
_SET_TS = {"$set": {
    "mod_duration": {
        "$round": [
            {"$divide": ["$duration", {"$add": [1, "$TEA"]}]},
            4,
        ],
    },
}}

_SET_MS = {"$set": {
    "metrics.mod_duration": {
        "$round": [
            {"$divide": ["$metrics.duration", {"$add": [1, "$metrics.TEA"]}]},
            4,
        ],
    },
}}


def run(dry: bool) -> None:
    client = get_mongo_client()

    # ─── Trading.TimeSales ──────────────────────────────────────────
    col_ts = client["Trading"]["TimeSales"]
    q_ts = {
        "duration": {"$gt": 0},
        "TEA": {"$exists": True, "$ne": None, "$gt": -1},
        "mod_duration": {"$exists": False},
    }
    n_ts = col_ts.count_documents(q_ts)
    logger.info("TimeSales candidatos: %d", n_ts)

    # ─── Trading.MarketSnapshot ─────────────────────────────────────
    col_ms = client["Trading"]["MarketSnapshot"]
    q_ms = {
        "metrics.duration": {"$gt": 0},
        "metrics.TEA": {"$exists": True, "$ne": None, "$gt": -1},
        "metrics.mod_duration": {"$exists": False},
    }
    n_ms = col_ms.count_documents(q_ms)
    logger.info("MarketSnapshot candidatos: %d", n_ms)

    if dry:
        return

    if n_ts > 0:
        r = col_ts.update_many(q_ts, [_SET_TS])
        logger.info("TimeSales modified=%d", r.modified_count)

    if n_ms > 0:
        r = col_ms.update_many(q_ms, [_SET_MS])
        logger.info("MarketSnapshot modified=%d", r.modified_count)

    logger.info("Backfill completo.")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry", action="store_true", help="Solo cuenta candidatos")
    args = parser.parse_args()
    run(dry=args.dry)


if __name__ == "__main__":
    main()
