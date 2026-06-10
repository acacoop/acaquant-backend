"""cleanup_cedears_timesales.py — vacía el Time & Sales intradía de CEDEARs.

Trading.CedearsTimeSales es una colección INTRADÍA: el motor_cedears infiere
trades durante la rueda (tape para el scanner) y este job la borra al cierre,
para que nunca arrastre data vieja entre ruedas (es solo para intraday).

El endpoint igual filtra por fecha de hoy, así que un residuo no se mostraría;
esto mantiene la colección chica y limpia.

Cron: post-cierre (20:10 UTC = 17:10 ART, L-V — motores paran 20:05).

Uso:
    python -m jobs.cleanup_cedears_timesales
    python -m jobs.cleanup_cedears_timesales --dry
"""
from __future__ import annotations

import logging
import sys

from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("CleanupCedearsTimeSales")


def run(dry: bool = False) -> int:
    col = get_mongo_client()["Trading"]["CedearsTimeSales"]
    n = col.estimated_document_count()
    if dry:
        logger.info("[DRY] borraría %d trades de Trading.CedearsTimeSales", n)
        return n
    res = col.delete_many({})
    logger.info("Vaciada Trading.CedearsTimeSales: %d trades borrados", res.deleted_count)
    return res.deleted_count


def main() -> int:
    dry = "--dry" in sys.argv
    from core.job_runs import JobRunLogger
    with JobRunLogger("cleanup_cedears_timesales") as jr:
        n = run(dry=dry)
        jr.set_stat("borrados", n)
        jr.set_stat("dry", dry)
    return 0


if __name__ == "__main__":
    sys.exit(main())
