"""scripts/drop_mongo_operaciones.py — dropea Mongo `CashFlow.Operaciones` (+ el
rollup `CashFlow.OpsSerieDiaria`), ya deprecadas: la fuente única es SQL
`operaciones.operaciones` (la escriben directo jobs/operaciones_informes.py y
jobs/fci_bilateral.py; las vistas /ops/* + comercial leen SQL; las series se
agregan en vivo, sin rollup; sync_operaciones y ops_rollup eliminados).

    python -m scripts.drop_mongo_operaciones            # PREVIEW (cuenta, no borra)
    python -m scripts.drop_mongo_operaciones --commit   # DROPEA

Irreversible. Antes de correr con --commit:
  1. Confirmá flags SQL en prod (OPERACIONES_SQL, COMERCIAL_SQL).
  2. Corré el writer al menos una vez (jobs.operaciones_informes) y verificá que
     SQL operaciones.operaciones tiene los boletos del día (no solo el histórico).
El dato histórico ya vive en operaciones.operaciones (lo sincronizó sync_operaciones).
"""
from __future__ import annotations

import sys

from core.mongo import get_mongo_client

_COLS = ["Operaciones", "OpsSerieDiaria"]


def main() -> None:
    commit = "--commit" in sys.argv
    db = get_mongo_client()["CashFlow"]
    existentes = set(db.list_collection_names())
    print("\n=== Mongo CashFlow (migración Operaciones→SQL) ===")
    for c in _COLS:
        n = db[c].estimated_document_count() if c in existentes else 0
        print(f"   {c:<16} {'existe' if c in existentes else 'NO existe':<10} docs≈{n}")
    if not commit:
        print("\n[PREVIEW] no se borró nada. Repetí con --commit.\n")
        return
    for c in _COLS:
        if c in existentes:
            db.drop_collection(c)
            print(f"   ✓ dropeada CashFlow.{c}")
    print("\n[COMMIT] listo. Chau Mongo Operaciones + OpsSerieDiaria.\n")


if __name__ == "__main__":
    main()
