"""$unset de campos legacy en Trading.MarketSnapshot.

`top_trades` y `recent_trades` los persistía engines/valores.py pero
NADIE los consume (ni frontend ni MCP ni services). Eran payload muerto.
Ya removidos del código del motor; este script limpia los docs viejos
que aún tienen esos campos en Mongo.

Uso:
    python -m scripts.cleanup_marketsnapshot_legacy            # dry-run (default)
    python -m scripts.cleanup_marketsnapshot_legacy --apply    # ejecuta el $unset
"""
from __future__ import annotations

import argparse
import logging

from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("cleanup_marketsnapshot_legacy")

DB_NAME = "Trading"
COL_NAME = "MarketSnapshot"
LEGACY_FIELDS = ("top_trades", "recent_trades")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Ejecuta el $unset. Sin este flag, solo cuenta.")
    args = parser.parse_args()

    col = get_mongo_client()[DB_NAME][COL_NAME]

    filtro = {"$or": [{f: {"$exists": True}} for f in LEGACY_FIELDS]}
    n = col.count_documents(filtro)

    if n == 0:
        logger.info("Ningún doc tiene %s. Nada que hacer.", LEGACY_FIELDS)
        return 0

    logger.info("Docs con campos legacy (%s): %d", LEGACY_FIELDS, n)

    if not args.apply:
        logger.info("[dry] Corré con --apply para ejecutar el $unset.")
        return 0

    res = col.update_many(filtro, {"$unset": {f: "" for f in LEGACY_FIELDS}})
    logger.info("OK: matched=%d modified=%d", res.matched_count, res.modified_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
