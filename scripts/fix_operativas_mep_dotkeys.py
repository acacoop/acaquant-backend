"""Repara las operativas MEP que quedaron SIN enlazar a sus dos patas.

Problema (fix en api/services/operativa_mep.py::aplicar_fields):
`_persistir_resultado_operativa` escribía las claves con dot-notation de Mongo
(`"buy.cl_ord_id"`, `"sell.cl_ord_id"`) y el upsert a Postgres las mergeaba
PLANAS. Resultado: en el jsonb quedó una clave de primer nivel llamada
literalmente `"buy.cl_ord_id"` y el `buy.cl_ord_id` anidado siguió en None.

Como el listado del día joinea `operaciones.ordenes_live` por ese id anidado,
las operativas viejas muestran BUY / SELL / USD EFECT / MEP EFECT vacías. Los
ids no se perdieron: están en la clave plana. Este script los mueve a su lugar.

Qué hace, exactamente:
  - Lee SOLO las filas cuyo `data` tiene la clave plana (filtro por índice de
    contención jsonb `?`, no un scan del historial entero).
  - Para cada una: mueve `data->'buy.cl_ord_id'` a `data->'buy'->'cl_ord_id'`
    (idem sell) y BORRA la clave plana. No toca ningún otro campo.
  - No pisa un id anidado que ya exista (por si el fix ya lo escribió bien).

Es idempotente: correrlo dos veces no cambia nada la segunda vez (después de
la primera pasada ya no quedan claves planas). Read-only sobre el broker.

Uso:
    python -m scripts.fix_operativas_mep_dotkeys            # aplica
    python -m scripts.fix_operativas_mep_dotkeys --dry-run  # solo reporta
"""
from __future__ import annotations

import argparse
import logging

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from core.postgres import get_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("FixOperativasMepDotKeys")

_PLANAS = ("buy.cl_ord_id", "sell.cl_ord_id")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="reporta sin escribir")
    args = ap.parse_args()

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        # `data ?| array[...]` = el jsonb tiene ALGUNA de esas claves de 1er nivel.
        cur.execute(
            "SELECT id, data FROM operaciones.operativas_mep "
            "WHERE data ?| %s ORDER BY ts",
            (list(_PLANAS),),
        )
        filas = cur.fetchall()
        logger.info("Operativas con claves planas: %d", len(filas))

        reparadas = 0
        for fila in filas:
            data = dict(fila["data"] or {})
            cambio = False
            for plana in _PLANAS:
                if plana not in data:
                    continue
                head, _, tail = plana.partition(".")
                valor = data.pop(plana)
                cambio = True
                sub = dict(data.get(head) or {})
                if sub.get(tail):
                    # Ya estaba bien anidado: la clave plana era basura duplicada.
                    logger.info("%s: %s ya anidado (%s) — solo borro la plana",
                                fila["id"], plana, sub.get(tail))
                    continue
                sub[tail] = valor
                data[head] = sub
                logger.info("%s: %s → %s.%s = %s", fila["id"], plana, head, tail, valor)
            if not cambio:
                continue
            reparadas += 1
            if not args.dry_run:
                cur.execute(
                    "UPDATE operaciones.operativas_mep SET data = %s WHERE id = %s",
                    (Jsonb(data), fila["id"]),
                )
        if args.dry_run:
            conn.rollback()
            logger.info("DRY-RUN: %d operativas se hubieran reparado (nada escrito)", reparadas)
        else:
            conn.commit()
            logger.info("Reparadas %d operativas", reparadas)


if __name__ == "__main__":
    main()
