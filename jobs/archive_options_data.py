"""archive_options_data.py — purga de mercado.options_data (SQL).

Borra los ticks con `ts` < hoy 00:00 ART → la tabla queda solo con la rueda en
curso. Es el motivo de existir del job: mantener acotada la tabla de ticks de
opciones (el chart intradía solo muestra el día; el histórico diario vive en
`mercado.options_data_hist`, que arma jobs/options_rollup).

SQL-NATIVE (decomiso Mongo): `Opciones.Data` (Mongo) fue dropeada — el motor escribe
los ticks a `mercado.options_data`. Ya no hay export a JSON (la fuente es SQL).

Comandos:
    python -m jobs.archive_options_data            # dry-run (solo cuenta)
    python -m jobs.archive_options_data --apply    # ejecuta el DELETE
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from core.postgres import get_pool

logger = logging.getLogger("archive_options_data")

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def run(apply: bool) -> int:
    # `ts` es naive ART (== datetime.now() del motor), así que el corte es contra
    # hoy ART SIN tz.
    hoy_ar = datetime.now(AR_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    hoy_naive = hoy_ar.replace(tzinfo=None)

    with get_pool().connection() as cn, cn.cursor() as cur:
        cur.execute("SELECT count(*) FROM mercado.options_data")
        total = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM mercado.options_data WHERE ts < %s", (hoy_naive,))
        a_borrar = cur.fetchone()[0]

        logger.info("Total ticks en mercado.options_data : %d", total)
        logger.info("Ticks con ts < %s : %d", hoy_ar.strftime("%Y-%m-%d %H:%M %Z"), a_borrar)

        if not apply:
            logger.info("[DRY-RUN] NO borra. Pasá --apply para ejecutar.")
            return 0

        cur.execute("DELETE FROM mercado.options_data WHERE ts < %s", (hoy_naive,))
        borrados = cur.rowcount or 0
        cur.execute("SELECT count(*) FROM mercado.options_data")
        restantes = cur.fetchone()[0]

    logger.info("🗑️  Borrados: %d ticks (ts < hoy ART)", borrados)
    logger.info("Quedan en la tabla: %d ticks", restantes)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true",
                        help="Ejecuta de verdad. Sin este flag es dry-run.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return run(apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
