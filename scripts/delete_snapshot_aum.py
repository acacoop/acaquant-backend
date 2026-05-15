"""delete_snapshot_aum.py — borra un snapshot completo de Valuaciones.AuM.

Para cuando un snapshot quedó incompleto (corrió mal) y hay que
rehacerlo desde cero — borrás el snapshot y volvés a correr
jobs.aum_backfill para esa fecha.

Dry-run por default: solo cuenta cuántos docs hay. Pasá --apply para
borrar de verdad.

Corre:  python -m scripts.delete_snapshot_aum --snapshot 2026-05-05
        python -m scripts.delete_snapshot_aum --snapshot 2026-05-05 --apply
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True,
                    help="fecha_snapshot a borrar (YYYY-MM-DD)")
    ap.add_argument("--apply", action="store_true", help="borra de verdad")
    args = ap.parse_args()

    col = get_mongo_client()["Valuaciones"]["AuM"]
    n = col.count_documents({"fecha_snapshot": args.snapshot})

    print(f"fecha_snapshot:                            {args.snapshot!r}")
    print(f"Docs en Valuaciones.AuM con ese snapshot:  {n}")

    if n == 0:
        print("\nNada que borrar.")
        return

    if not args.apply:
        print(f"\nDRY-RUN. Se borrarian {n} docs. Corre con --apply para borrar.")
        return

    res = col.delete_many({"fecha_snapshot": args.snapshot})
    print(f"\nBORRADO: {res.deleted_count} docs eliminados de Valuaciones.AuM.")
    print(f"Para rehacer el snapshot: python -m jobs.aum_backfill {args.snapshot}")


if __name__ == "__main__":
    main()
