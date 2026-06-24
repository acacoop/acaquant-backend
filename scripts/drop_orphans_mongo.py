"""drop_orphans_mongo.py — dropea colecciones Mongo ya migradas/huérfanas a SQL.

⚠️ Correr SOLO después de verificar que la vista correspondiente lee bien de SQL.

Ya dropeadas (cutover completo: read SQL-only + write SQL-native + sync eliminado):
  - Trading.SnapshotsCierre        (2026-06-24)
  - Trading.PreciosAcciones        (2026-06-24) — 1ª colección VIVA. Writers SQL-native
    (precios_acciones_daily/backfill), lectores SQL (scanner_sql, pivot_points, rv_motor).

Pendiente de drop (cutover hecho — verificar antes de --apply):
  - Trading.AdrSnapshot — jobs/adr_live escribe mercado.adr_snapshot SQL-native
    (write_native) + registra en JobRuns (run_tipo="adr_live"). Lector Mongo solo en
    scanner.py (gateado SCANNER_SQL=0 → dormido en prod). sync_adr_snapshot eliminado.
    ⚠️ ANTES de dropear: deployar + verificar que el SCANNER (vista ADR: precio USD live
    + 1D) sigue fresco tras una corrida de adr_live (cada 15m). Ahí --apply.

Idempotente: dropear una colección ya borrada = no-op (0 docs). Dry-run por default.
    python -m scripts.drop_orphans_mongo            # cuenta (dry-run)
    python -m scripts.drop_orphans_mongo --apply    # DROPEA
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

_ORPHANS: list[tuple[str, str]] = [
    ("Trading", "AdrSnapshot"),
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
