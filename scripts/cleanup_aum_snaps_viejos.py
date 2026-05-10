"""
cleanup_aum_snaps_viejos.py — borra los snapshots del 1° de mes que
quedaron como ruido tras los backfills EOM (jobs/aum_backfill_historico).

Cada uno representa el INICIO del mes y ya está funcionalmente
reemplazado por el snap del último día del mismo mes calendario
(2025-07-31, 2025-08-31, ..., 2026-02-28).

Uso:
    # Dry-run (default)
    python -m scripts.cleanup_aum_snaps_viejos

    # Aplicar
    python -m scripts.cleanup_aum_snaps_viejos --yes
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

_FECHAS_A_BORRAR = [
    "2025-07-01",
    "2025-08-01",
    "2025-09-01",
    "2025-10-01",
    "2025-11-01",
    "2025-12-01",
    "2026-01-01",
    "2026-02-01",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true",
                    help="Aplicar el delete (default: dry-run)")
    args = ap.parse_args()

    client = get_mongo_client()
    coll = client["Valuaciones"]["AuM"]

    print("\n  Fechas a borrar:")
    print(f"  {'fecha_snapshot':<14} {'n_docs':>10}")
    print(f"  {'-' * 14} {'-' * 10}")
    total = 0
    for fecha in _FECHAS_A_BORRAR:
        n = coll.count_documents({"fecha_snapshot": fecha})
        total += n
        print(f"  {fecha:<14} {n:>10}")
    print(f"  {'-' * 14} {'-' * 10}")
    print(f"  {'TOTAL':<14} {total:>10}")

    if not args.yes:
        print("\n  (DRY-RUN — pasar --yes para borrar)")
        return

    print(f"\n  Borrando {total} docs...", flush=True)
    res = coll.delete_many({"fecha_snapshot": {"$in": _FECHAS_A_BORRAR}})
    print(f"  ✅ deleted={res.deleted_count}")


if __name__ == "__main__":
    main()
