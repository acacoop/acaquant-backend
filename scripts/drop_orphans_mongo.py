"""drop_orphans_mongo.py — dropea colecciones Mongo HUÉRFANAS (nadie lee NI escribe).

Verificado 2026-06-23:
  - Clientes.ComercialCache (58k) — el rollup `jobs/comercial_rollup` se ELIMINÓ (nadie
    escribe); el informe comercial lee SQL en vivo (comercial_sql, flag COMERCIAL_SQL).
    Solo el path Mongo MUERTO de comercial.py la lee (gateado off). Huérfano.
  - Valuaciones.TenenciaHD (56) — el job `jobs/tenencia_hd` se ELIMINÓ (nadie escribe);
    la vista Back Office lee portafolio.tenencia (SQL) directo. Huérfano.

Dry-run por default.
    python -m scripts.drop_orphans_mongo            # cuenta
    python -m scripts.drop_orphans_mongo --apply    # DROPEA
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

_ORPHANS = [("Clientes", "ComercialCache"), ("Valuaciones", "TenenciaHD")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="dropear (default: dry-run)")
    args = ap.parse_args()

    cli = get_mongo_client()
    for db, coll in _ORPHANS:
        n = cli[db][coll].estimated_document_count()
        print(f"{db}.{coll}: {n:,} docs")
        if args.apply:
            cli[db][coll].drop()
            print(f"  ✅ {db}.{coll} DROPEADA")

    if not args.apply:
        print("\n(DRY-RUN — nada borrado. Correr con --apply.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
