"""diag_aum_uniqueness.py — inventario de fechas en Valuaciones.AuM.

Reporta:
  1. Lista de todos los `fecha_snapshot` únicos de Valuaciones.AuM,
     ordenados ascendente.
  2. Por cada fecha_snapshot, cuántos `timestamp` únicos hay.
     (Para ver si el job corrió 1× o N× ese día — un job sano persiste
     con un único timestamp por corrida; ver múltiples timestamps en
     la misma fecha indica reruns o backfills sin cleanup.)
  3. Total de fechas distintas y total de docs.

Uso:
    python -m scripts.diag_aum_uniqueness

    # Para ver sólo un rango (post-cierto cutoff):
    python -m scripts.diag_aum_uniqueness --desde 2026-01-01

    # Para ver sólo fechas con MÚLTIPLES timestamps (reruns):
    python -m scripts.diag_aum_uniqueness --solo-multi
"""
from __future__ import annotations

import argparse

from api.db import get_db_valuaciones


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--desde", help="Fecha desde YYYY-MM-DD (inclusive)")
    parser.add_argument("--hasta", help="Fecha hasta YYYY-MM-DD (inclusive)")
    parser.add_argument(
        "--solo-multi",
        action="store_true",
        help="Filtrar a fechas con > 1 timestamp único",
    )
    args = parser.parse_args()

    db_v = get_db_valuaciones()
    coll = db_v["AuM"]

    match: dict = {}
    if args.desde or args.hasta:
        rango: dict = {}
        if args.desde:
            rango["$gte"] = args.desde
        if args.hasta:
            rango["$lte"] = args.hasta
        match["fecha_snapshot"] = rango

    # 2-step group: primero deduplica (fecha, timestamp), después junta
    # los timestamps únicos por fecha en una lista. $addToSet en el outer
    # group es seguro acá porque el inner group ya colapsó duplicados —
    # la lista por fecha tiene N items con N = timestamps únicos del día
    # (típicamente 1, raro pasar de 5).
    pipeline: list[dict] = []
    if match:
        pipeline.append({"$match": match})
    pipeline += [
        {"$group": {
            "_id": {"fecha": "$fecha_snapshot", "ts": "$timestamp"},
            "docs_count": {"$sum": 1},
        }},
        {"$group": {
            "_id":                  "$_id.fecha",
            "timestamps":           {"$addToSet": "$_id.ts"},
            "n_docs_total":         {"$sum": "$docs_count"},
        }},
        {"$sort": {"_id": 1}},
    ]

    print("=== Valuaciones.AuM uniqueness ===")
    if match:
        print(f"  filtro: {match}")
    print()

    rows = list(coll.aggregate(pipeline, allowDiskUse=True))

    if args.solo_multi:
        rows = [r for r in rows if len(r.get("timestamps", [])) > 1]

    if not rows:
        print("  (sin resultados)")
        return

    total_docs = 0
    n_multi = 0
    print(f"  {'fecha_snapshot':<14} {'n_ts':>4} {'n_docs':>10}  timestamps")
    print(f"  {'-' * 14} {'-' * 4} {'-' * 10}  {'-' * 40}")
    for r in rows:
        fecha = r["_id"]
        ts_list = sorted(str(t) for t in r.get("timestamps", []))
        n_ts = len(ts_list)
        n_docs = r["n_docs_total"]
        total_docs += n_docs
        if n_ts > 1:
            n_multi += 1
        marker = "  ⚠" if n_ts > 1 else ""
        ts_str = ", ".join(ts_list)
        print(f"  {fecha:<14} {n_ts:>4} {n_docs:>10}  {ts_str}{marker}")
    print()
    print(f"  total fechas: {len(rows)}")
    print(f"  total docs:   {total_docs}")
    if n_multi > 0:
        print(f"  fechas con múltiples timestamps (⚠): {n_multi}")


if __name__ == "__main__":
    main()
