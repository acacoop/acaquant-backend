"""scripts/drop_mongo_comitentes.py — dropea Mongo `Clientes.Comitentes`, ya deprecada:
fuente única SQL `clientes.comitentes` (ingesta sync_comitentes + panel Manager escriben
SQL; readers migrados; sync_dims_clientes eliminado).

    python -m scripts.drop_mongo_comitentes            # PREVIEW
    python -m scripts.drop_mongo_comitentes --commit   # DROPEA

Irreversible. El dato vive en clientes.comitentes (+ cuentas/operadores).
"""
from __future__ import annotations

import sys

from core.mongo import get_mongo_client


def main() -> None:
    commit = "--commit" in sys.argv
    db = get_mongo_client()["Clientes"]
    existe = "Comitentes" in db.list_collection_names()
    n = db["Comitentes"].estimated_document_count() if existe else 0
    print(f"\n=== Mongo Clientes.Comitentes ===\n   {'existe' if existe else 'NO existe'}  ·  docs≈{n}")
    if not commit:
        print("\n[PREVIEW] no se borró nada. Repetí con --commit.\n")
        return
    if existe:
        db.drop_collection("Comitentes")
        print("   ✓ dropeada Clientes.Comitentes")
    print("\n[COMMIT] listo. Chau Mongo Comitentes.\n")


if __name__ == "__main__":
    main()
