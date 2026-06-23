"""drop_orphans_mongo.py — dropea colecciones Mongo HUÉRFANAS (nadie lee NI escribe).

Verificado 2026-06-23 por grep de acceso REAL a la colección en TODO el repo (no
menciones en comentarios), tras descartar 3 falsos positivos de una auditoría automática
(OnsIgnoradas/PortfolioSnapshotLog/PyRofexDiscovery SÍ están vivas):
  - Manager.ChangeLog — sin writer ni reader. Solo aparecía en la lista de retención de
    `scripts/db_maintenance.py` (ya removida). Huérfana.
  - CashFlow.ValuacionFlujoExcluidos — CERO referencias en el repo entero. Huérfana.

NO incluye CashFlow.Flujo: es zombie (job vivo, sin reader de vista — /contrapartes lee
SQL operaciones.operaciones), pero matarla requiere bajar el job flujo_contrapartes
(cron + monitores) → se hace aparte, con confirmación.

IRREVERSIBLE: dropear borra los datos. Dry-run por default muestra el conteo primero.
    python -m scripts.drop_orphans_mongo            # cuenta (dry-run)
    python -m scripts.drop_orphans_mongo --apply    # DROPEA
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

_ORPHANS = [("Manager", "ChangeLog"), ("CashFlow", "ValuacionFlujoExcluidos")]


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
