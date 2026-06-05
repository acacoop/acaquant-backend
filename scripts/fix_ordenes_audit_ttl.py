"""Saca el TTL de Operaciones.OrdenesAudit.ts_1 (audit de órdenes → guardar siempre).

El índice ts_1 tenía expireAfterSeconds=7776000 (90d) puesto fuera de banda en
Atlas → el audit de órdenes se auto-borraba a los 90 días. Decisión 2026-06-05:
la traza de auditoría se conserva completa. Este script dropea el TTL y recrea
ts_1 PLANO (queda alineado con el create_index del motor → sin más
IndexOptionsConflict que tumbaba el arranque).

Seguro: dropear un índice NO borra documentos (solo el índice). Colección chica
(~770 docs) → op barata, no escanea (REGLA #4 OK). Idempotente: si ts_1 ya es
plano, no hace nada.

Uso:
    python -m scripts.fix_ordenes_audit_ttl
    python -m scripts.fix_ordenes_audit_ttl --dry-run
"""
from __future__ import annotations

import argparse

from pymongo import ASCENDING

from core.mongo import get_mongo_client


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="No toca nada; solo muestra.")
    args = ap.parse_args()

    col = get_mongo_client()["Operaciones"]["OrdenesAudit"]
    ts_ix = next((ix for ix in col.list_indexes() if ix.get("name") == "ts_1"), None)

    if not ts_ix:
        print("ts_1 no existe → lo creo plano.")
        if not args.dry_run:
            col.create_index([("ts", ASCENDING)])
        return 0

    ttl = ts_ix.get("expireAfterSeconds")
    if ttl is None:
        print("ts_1 ya es plano (sin TTL). Nada que hacer.")
        return 0

    print(f"ts_1 tiene TTL={ttl}s (~{ttl // 86400}d) → lo saco y recreo plano.")
    if args.dry_run:
        print("[dry-run] no se tocó nada.")
        return 0

    col.drop_index("ts_1")
    col.create_index([("ts", ASCENDING)])   # plano → matchea el create_index del motor
    print("✅ TTL removido. ts_1 ahora es índice plano; el audit ya no se auto-borra.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
