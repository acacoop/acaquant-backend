"""drop_orphans_mongo.py — dropea colecciones Mongo ya migradas/huérfanas a SQL.

⚠️ Correr SOLO después de verificar que la vista correspondiente lee bien de SQL.

Cutover 2026-06-24 (read SQL-only + write SQL-native + sync eliminado):
  - Trading.PreciosAcciones — jobs/precios_acciones_daily + scripts/backfill_precios_acciones
    escriben mercado.precios_acciones SQL-native (write_native). Lectores migrados a SQL:
    scanner_sql, quant/pivot_points (4 lecturas) y api/services/rv_motor (Estrategia RV).
    sync_precios_acciones eliminado. El path Mongo de scanner.py SOLO se usa con
    SCANNER_SQL=0 → debe quedar SCANNER_SQL=1 (prod ya lo tiene).
    ⚠️ ANTES de dropear: deployar + verificar en la próxima rueda que andan: SCANNER vista
    ADR (7D/15R/MTD/YTD), panel PIVOTS, y Estrategia RV (matriz de correlación). Ahí --apply.

Idempotente: dropear una colección ya borrada = no-op (0 docs). Dry-run por default.
    python -m scripts.drop_orphans_mongo            # cuenta (dry-run)
    python -m scripts.drop_orphans_mongo --apply    # DROPEA
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

_ORPHANS = [
    ("Trading", "PreciosAcciones"),
]


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
