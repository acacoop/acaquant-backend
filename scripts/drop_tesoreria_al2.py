"""scripts/drop_tesoreria_al2.py — baja definitiva de la tab SALDO AL2.

La funcionalidad se eliminó del código (vista, endpoint, job y cron). Lo único que
queda viva es la tabla en Postgres, que este script borra.

Es DESTRUCTIVO e irreversible: `operaciones.tesoreria_al2` era la ÚNICA tabla de
movimientos de tesorería que se persistía, así que sus filas no se pueden
reconstruir (Aunesa se pide día por día y la vista del día es live). Por eso el
borrado NO es el default: primero mostrá qué hay, y recién después confirmá.

Uso:
    python -m scripts.drop_tesoreria_al2              # solo informa (dry-run)
    python -m scripts.drop_tesoreria_al2 --confirmar  # DROPea la tabla
"""
from __future__ import annotations

import argparse
import logging

from core.postgres import get_pool

_log = logging.getLogger("drop_tesoreria_al2")
TABLA = "operaciones.tesoreria_al2"


def _estado() -> tuple[bool, int]:
    """(existe, filas) de la tabla."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (TABLA,))
        existe = bool(cur.fetchone()[0])
        if not existe:
            return False, 0
        cur.execute(f"SELECT COUNT(*) FROM {TABLA}")
        return True, int(cur.fetchone()[0])


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Borra la tabla de la tab SALDO AL2")
    ap.add_argument("--confirmar", action="store_true",
                    help="ejecuta el DROP (sin esto solo informa)")
    args = ap.parse_args()

    existe, filas = _estado()
    if not existe:
        _log.info("%s no existe — nada que hacer.", TABLA)
        return 0

    _log.info("%s existe · %d filas", TABLA, filas)
    if not args.confirmar:
        _log.info("DRY-RUN — no se borró nada. Para borrarla de verdad:")
        _log.info("    python -m scripts.drop_tesoreria_al2 --confirmar")
        return 0

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {TABLA}")
        conn.commit()
    _log.info("LISTO — %s borrada (se fueron %d filas).", TABLA, filas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
