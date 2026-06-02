"""Diag READ-ONLY: qué colección de Contrapartes usar para "id_cuenta en
Contrapartes → PJ GRANDE", y cuántas Comitentes activas matchean.

Inspecciona las dos candidatas (campos + cantidad de ids distintos + overlap con
Comitentes activas) para no asumir cuál es. NO escribe nada.

Uso:
    python -m scripts.diag_contrapartes_ids
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read


def _ids(coll, campo: str) -> set[str]:
    return {str(v).strip() for v in coll.distinct(campo) if v not in (None, "")}


def main() -> None:
    cli = get_mongo_client_read()

    activas = {str(c["id_cuenta"]).strip()
               for c in cli["Clientes"]["Comitentes"].find(
                   {"estado": "Activa"}, {"_id": 0, "id_cuenta": 1}) if c.get("id_cuenta")}
    print(f"Comitentes activas: {len(activas)}\n")

    candidatos = [
        ("CashFlow", "Contrapartes", "cuenta"),
        ("CuentasAPI", "ContrapartesAPI", "id_cuenta"),
    ]
    for db, col, campo in candidatos:
        try:
            coll = cli[db][col]
            n_docs = coll.estimated_document_count()
            sample = coll.find_one({}, {"_id": 0})
            keys = sorted(sample.keys()) if sample else []
            ids = _ids(coll, campo)
            overlap = ids & activas
            print(f"── {db}.{col}  (campo '{campo}') ──")
            print(f"   docs: {n_docs} | ids distintos: {len(ids)} | ∩ Comitentes activas: {len(overlap)}")
            print(f"   keys del doc: {keys}")
            print(f"   ejemplos de id: {sorted(ids)[:8]}")
        except Exception as e:
            print(f"── {db}.{col}: ERROR {e}")
        print()

    print("(read-only: no se escribió nada)")


if __name__ == "__main__":
    main()
