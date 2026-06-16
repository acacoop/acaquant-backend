"""scripts/drop_mongo_contrapartes.py — dropea Mongo `CashFlow.Contrapartes`, ya
deprecada: la fuente única es SQL `clientes.contrapartes` (editor + conciliador +
_aum_filters + flujo leen/escriben SQL; sync_contrapartes eliminado).

    python -m scripts.drop_mongo_contrapartes            # PREVIEW (cuenta, no borra)
    python -m scripts.drop_mongo_contrapartes --commit   # DROPEA

Irreversible. El dato vive en clientes.contrapartes (copiado por migrar_contrapartes_a_sql).
"""
from __future__ import annotations

import sys

from core.mongo import get_mongo_client


def main() -> None:
    commit = "--commit" in sys.argv
    db = get_mongo_client()["CashFlow"]
    existe = "Contrapartes" in db.list_collection_names()
    n = db["Contrapartes"].estimated_document_count() if existe else 0
    print(f"\n=== Mongo CashFlow.Contrapartes ===\n   {'existe' if existe else 'NO existe'}  ·  docs≈{n}")
    if not commit:
        print("\n[PREVIEW] no se borró nada. Repetí con --commit.\n")
        return
    if existe:
        db.drop_collection("Contrapartes")
        print("   ✓ dropeada CashFlow.Contrapartes")
    print("\n[COMMIT] listo. Chau Mongo Contrapartes.\n")


if __name__ == "__main__":
    main()
