"""backfill_segmentos_upper.py — normaliza nivel_1..5 a MAYÚSCULAS en Comitentes.

Convención del sistema: TODOS los valores de segmentación viven en MAYÚSCULAS
para evitar duplicados por capitalización ("Productores" vs "PRODUCTORES"
quedaban como dos categorías distintas en la distribución por nivel).

Este script se corre una vez sobre lo que ya está en Mongo. Los endpoints
`POST /api/manager/clientes/bulk` y `PATCH /api/manager/clientes` también
hacen `.upper()` antes de persistir → la convención se mantiene sin
re-backfill (ver `api/routers/manager/clientes.py`).

Idempotente: `$toUpper` sobre un string ya en mayúsculas es no-op.

Uso:
    python -m scripts.backfill_segmentos_upper                  # dry-run
    python -m scripts.backfill_segmentos_upper --apply
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

DB = "Clientes"
COL = "Comitentes"
CAMPOS = ("nivel_1", "nivel_2", "nivel_3", "nivel_4", "nivel_5")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="ejecutar (default: dry-run)")
    args = ap.parse_args()

    col = get_mongo_client()[DB][COL]

    print("Diagnóstico por campo (valores únicos con minúsculas → ejemplos):\n")
    total_cambios = 0
    for campo in CAMPOS:
        valores = [v for v in col.distinct(campo) if isinstance(v, str) and v]
        cambian = [v for v in valores if v != v.upper()]
        # Cantidad de docs afectados (no de valores únicos).
        n_docs = col.count_documents(
            {campo: {"$type": "string", "$ne": ""}, "$expr": {"$ne": [{"$toUpper": f"${campo}"}, f"${campo}"]}}
        )
        total_cambios += n_docs
        print(f"  {campo:<10s} únicos: {len(valores):>4d}  con mayús pendiente: {len(cambian):>3d}  → {n_docs} docs")
        for v in cambian[:5]:
            print(f"       e.g. '{v}' → '{v.upper()}'")

    print(f"\nTotal docs a modificar (suma sobre los 5 campos): {total_cambios}")

    if not args.apply:
        print("\n(dry-run) — pasar --apply para ejecutar.")
        return

    print("\nAplicando…")
    for campo in CAMPOS:
        r = col.update_many(
            {campo: {"$type": "string"}},
            [{"$set": {campo: {"$toUpper": f"${campo}"}}}],
        )
        print(f"  {campo}: matched={r.matched_count} modified={r.modified_count}")

    print("OK.")


if __name__ == "__main__":
    main()
