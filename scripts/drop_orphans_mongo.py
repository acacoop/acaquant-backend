"""drop_orphans_mongo.py — dropea colecciones Mongo ya migradas/huérfanas a SQL.

⚠️ Correr SOLO después de verificar que la vista correspondiente lee bien de SQL.

Cutover verificado 2026-06-24 (read SQL-only + write SQL-native + sync eliminado):
  - Trading.SnapshotsCierre — jobs/snapshot_cierre.py escribe SQL-native (2 tablas:
    mercado.snapshots_cierre último-por-ticker = fallback de precio del PnL, y
    snapshots_cierre_hist = histórico). 6 lectores migrados a SQL (pnl, renta_fija,
    analitica, carry_trade, fair_value). ⚠️ ANTES de dropear: correr
    `python -m jobs.sync_postgres --full` para garantizar el histórico completo en SQL,
    y verificar PnL + histórico de curva + carry + fair_value.

Idempotente: dropear una colección ya borrada = no-op (0 docs). Dry-run por default.
    python -m scripts.drop_orphans_mongo            # cuenta (dry-run)
    python -m scripts.drop_orphans_mongo --apply    # DROPEA
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

_ORPHANS = [
    ("Trading", "SnapshotsCierre"),
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
