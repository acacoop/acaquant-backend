"""Migración one-shot: lleva Clientes.Comitentes a los 13 campos manuales nuevos.

Los docs ya poblados tienen los nombres viejos (segmento / sub_segmento /
sub_sub_segmento) y les faltan los 8 campos nuevos. Este script:
  1. Renombra segmento→nivel_1, sub_segmento→nivel_2, sub_sub_segmento→nivel_3
     (arregla los que estaban mal).
  2. Agrega en null (SOLO si faltan, no pisa lo cargado) nivel_4, nivel_5,
     primer_contacto_comercial, riesgo_la_ft, division, adc, dma, observaciones.

Idempotente: re-correr no rompe ni pisa datos. `sucursal`/`referido` no se tocan.

Uso:
    python -m scripts.migrate_comitentes_campos_manuales --dry-run
    python -m scripts.migrate_comitentes_campos_manuales
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

DB = "Clientes"
COL = "Comitentes"

RENAME = {"segmento": "nivel_1", "sub_segmento": "nivel_2", "sub_sub_segmento": "nivel_3"}
NUEVOS = [
    "nivel_4", "nivel_5", "primer_contacto_comercial", "riesgo_la_ft",
    "division", "adc", "dma", "observaciones",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="no escribe, solo reporta")
    args = ap.parse_args()

    col = get_mongo_client()[DB][COL]
    total = col.count_documents({})
    con_viejos = col.count_documents({"$or": [{k: {"$exists": True}} for k in RENAME]})
    faltan_nuevos = col.count_documents({"$or": [{f: {"$exists": False}} for f in NUEVOS]})
    print(f"Docs totales:                         {total}")
    print(f"Con campos viejos a renombrar:        {con_viejos}")
    print(f"A los que les falta algún campo nuevo: {faltan_nuevos}")

    if args.dry_run:
        print("(DRY-RUN — no se escribió nada)")
        return

    # 1) rename (no-op por doc si el campo viejo no existe)
    r1 = col.update_many({}, {"$rename": RENAME})
    print(f"rename viejos→nivel_1/2/3: matched={r1.matched_count} modified={r1.modified_count}")

    # 2) agregar nuevos en null solo si faltan (pipeline $ifNull → no pisa valores)
    pipeline = [{"$set": {f: {"$ifNull": [f"${f}", None]} for f in NUEVOS}}]
    r2 = col.update_many({}, pipeline)
    print(f"set nuevos en null:        matched={r2.matched_count} modified={r2.modified_count}")
    print("✓ Migración OK.")


if __name__ == "__main__":
    main()
