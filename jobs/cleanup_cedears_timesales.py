"""cleanup_cedears_timesales.py — vacía el Time & Sales intradía de CEDEARs.

mercado.cedears_time_sales (SQL) es una tabla INTRADÍA: el motor_cedears infiere
trades durante la rueda (tape para el scanner) y este job la vacía para que
nunca arrastre data vieja entre ruedas (es solo para intraday).

SQL-NATIVE (decomiso Mongo 2026-06-28): antes vaciaba Trading.CedearsTimeSales.
TRUNCATE ... RESTART IDENTITY resetea también la secuencia del id.

Cron: PRE-APERTURA del día siguiente (12:35 UTC = 09:35 ART, L-V — motores
arrancan 13:20). Antes corría al cierre (20:10 UTC) y la mesa perdía el
tape/chart 15 minutos después de cerrar (pedido 2026-07-14); borrando a la
mañana, el post-cierre queda disponible para revisar la rueda y la apertura
arranca limpia igual (el reader además filtra "solo hoy").

Uso:
    python -m jobs.cleanup_cedears_timesales
    python -m jobs.cleanup_cedears_timesales --dry
"""
from __future__ import annotations

import logging
import sys

from core.postgres import get_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("CleanupCedearsTimeSales")


def run(dry: bool = False) -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM cedears_time_sales")
        n = cur.fetchone()[0]
        if dry:
            logger.info("[DRY] borraría %d trades de mercado.cedears_time_sales", n)
            return n
        cur.execute("TRUNCATE cedears_time_sales RESTART IDENTITY")
    logger.info("Vaciada mercado.cedears_time_sales: %d trades borrados", n)
    return n


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
