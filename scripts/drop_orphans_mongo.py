"""drop_orphans_mongo.py — dropea colecciones Mongo ya migradas/huérfanas a SQL.

⚠️ Correr SOLO después de verificar que la vista correspondiente lee bien de SQL.

Cutovers verificados 2026-06-24 (read SQL-only + write SQL-native + sync eliminado):
  - CashFlow.Movimientos — vista FLUJOS lee operaciones.movimientos; jobs/cashflow.py
    escribe SQL-native. Verificar /operaciones → flujos antes de dropear.
  - CashFlow.Acreencias — back-office/acreencias + comercial/cobros-futuros leen
    operaciones.acreencias; jobs/acreencias.py escribe SQL-native (swap atómico).
  - Opciones.DataHistorica — chart de griegas (/griegas/opciones) lee
    mercado.options_data_hist; jobs/options_rollup.py escribe SQL-native.

Idempotente: dropear una colección ya borrada = no-op (0 docs). Dry-run por default.
    python -m scripts.drop_orphans_mongo            # cuenta (dry-run)
    python -m scripts.drop_orphans_mongo --apply    # DROPEA
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

_ORPHANS = [
    ("CashFlow", "Movimientos"),
    ("CashFlow", "Acreencias"),
    ("Opciones", "DataHistorica"),
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
