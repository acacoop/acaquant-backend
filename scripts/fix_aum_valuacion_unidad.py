"""Corrige `valuacion = cantidad * precio / 100` para todos los docs de
una `unidad` específica en `Valuaciones.AuM`.

Pensado para bonos donde Aunesa o un import manual dejó `valuacion = P*Q`
sin dividir por 100 (el divisor estándar para Títulos Públicos / Letras /
ON / etc.). Aplica a TODAS las fechas y cuentas que tengan esa unidad —
no toca el resto del AuM.

Privacidad: imprime solo conteos, no docs individuales.

Uso:
    python -m scripts.fix_aum_valuacion_unidad "[9327] D16E6" --dry-run
    python -m scripts.fix_aum_valuacion_unidad "[9327] D16E6"
"""
from __future__ import annotations

import argparse
import sys

from core.mongo import get_mongo_client


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("unidad", help='Match exacto del campo `unidad`. Ej: "[9327] D16E6"')
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview — no toca data. Muestra cuántos se actualizarían.")
    args = ap.parse_args()

    client = get_mongo_client()
    col = client["Valuaciones"]["AuM"]

    n = col.count_documents({"unidad": args.unidad})
    print(f"unidad: {args.unidad!r}")
    print(f"docs encontrados: {n}")
    if n == 0:
        print("No hay nada que actualizar.")
        return

    if args.dry_run:
        # Sample agregado por fecha — útil para ver el alcance sin exponer
        # importes individuales.
        rows = list(col.aggregate([
            {"$match": {"unidad": args.unidad}},
            {"$group": {"_id": "$fecha_snapshot", "n": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ]))
        print("\nDocs por fecha_snapshot:")
        for r in rows:
            print(f"  {r['_id']}: {r['n']}")
        print("\n--dry-run: nada se actualizó. Quitá el flag para correrlo en serio.")
        return

    # Update con aggregation pipeline (Mongo 4.2+): valuacion = (cantidad*precio)/100.
    result = col.update_many(
        {"unidad": args.unidad},
        [{"$set": {
            "valuacion": {"$divide": [
                {"$multiply": [
                    {"$ifNull": ["$cantidad", 0]},
                    {"$ifNull": ["$precio", 0]},
                ]},
                100,
            ]},
        }}],
    )
    print(f"\nmatched:  {result.matched_count}")
    print(f"modified: {result.modified_count}")
    print("\nPróximos pasos:")
    print("  python -m scripts.api_migrate aum")
    print("  python -m jobs.aum_resumen_fci --backfill   # solo si afecta FCI")
    print("  systemctl restart api.service")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python -m scripts.fix_aum_valuacion_unidad \"[9327] D16E6\" [--dry-run]")
        sys.exit(1)
    main()
