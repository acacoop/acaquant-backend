"""Limpia contratos vencidos de Trading.FuturosDLRSnapshot.

El motor (engines/futuros_dlr.py) hace ReplaceOne(upsert=True) por ticker
y nunca borra. Al vencer un contrato el doc queda fantasma con la última
info y la API lo devolvía con TNAs absurdos (ya filtrado en
api/services/derivados.py::get_futuros_dlr, pero la basura sigue en Mongo).

One-shot manual; B2 (cron) viene después.

Uso:
    python -m scripts.cleanup_futuros_dlr_vencidos          # dry-run (default)
    python -m scripts.cleanup_futuros_dlr_vencidos --apply  # ejecuta el delete
"""
from __future__ import annotations

import argparse
import logging
from datetime import date

from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("cleanup_futuros_dlr")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Ejecuta el delete. Sin este flag, solo lista.")
    args = parser.parse_args()

    hoy = date.today().strftime("%Y%m%d")
    col = get_mongo_client()["Trading"]["FuturosDLRSnapshot"]

    filtro = {"vencimiento": {"$lte": hoy}}
    vencidos = list(col.find(filtro, {"_id": 0, "ticker": 1, "vencimiento": 1,
                                      "last_price": 1, "updated_at": 1}))

    if not vencidos:
        logger.info("No hay contratos vencidos en FuturosDLRSnapshot. Nada que hacer.")
        return 0

    logger.info("Contratos vencidos a borrar (vencimiento <= %s):", hoy)
    for d in vencidos:
        logger.info(
            "  %-20s vto=%s last=%s updated=%s",
            d.get("ticker"), d.get("vencimiento"),
            d.get("last_price"), d.get("updated_at"),
        )

    if not args.apply:
        logger.info("[dry] %d doc(s) marcados — corré con --apply para borrar.", len(vencidos))
        return 0

    res = col.delete_many(filtro)
    logger.info("Borrados: %d doc(s).", res.deleted_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
