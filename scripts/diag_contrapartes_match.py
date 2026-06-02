"""diag_contrapartes_match.py — ¿el match contrapartes↔Operaciones funciona?

Read-only. Replica lo que hace /api/operaciones/flujo directo contra Mongo, para
ver si el problema es el match por cuenta, los datos, o el caché/API.

Uso (Droplet): venv/bin/python -m scripts.diag_contrapartes_match
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read


def main() -> None:
    db = get_mongo_client_read()["CashFlow"]
    cps = list(db["Contrapartes"].find(
        {"cuenta": {"$exists": True, "$ne": ""}},
        {"_id": 0, "cuenta": 1, "contraparte": 1, "segmento": 1},
    ))
    cuentas = [str(d.get("cuenta")).strip() for d in cps if d.get("cuenta") not in (None, "")]
    print(f"CashFlow.Contrapartes con cuenta: {len(cuentas)}")
    print(f"  muestra cuenta (formato): {[d.get('cuenta') for d in cps[:5]]}")
    print(f"  muestra segmento: {sorted({(d.get('segmento') or '') for d in cps})[:10]}")

    op = db["Operaciones"]
    print(f"\nOperaciones total: {op.estimated_document_count():,}")
    print(f"  muestra cuenta (formato): {[d.get('cuenta') for d in op.find({}, {'_id':0,'cuenta':1}).limit(5)]}")

    # ¿Cuántas operaciones matchean por cuenta ∈ contrapartes?
    n_match = op.count_documents({"cuenta": {"$in": cuentas}})
    print(f"\nOperaciones cuyo `cuenta` ∈ contrapartes: {n_match:,}")
    if n_match == 0:
        print("  ⚠ CERO → el match por cuenta no encuentra nada (formato distinto?).")
        # ¿Match si extraemos solo dígitos?
        import re
        cu_dig = {re.sub(r'\D', '', c) for c in cuentas if c}
        ej = list(op.aggregate([{"$group": {"_id": "$cuenta"}}, {"$limit": 2000}]))
        op_cu = {re.sub(r'\D', '', str(d['_id'] or '')) for d in ej}
        print(f"  por dígitos: {len(cu_dig & op_cu)} cuentas en común (de {len(cu_dig)} contrapartes)")

    # Muestra de un flujo (con los campos clave que el front necesita).
    sample = list(op.find(
        {"cuenta": {"$in": cuentas}},
        {"_id": 0, "boleto": 1, "tipo_operacion": 1, "cuenta": 1, "denominacion": 1,
         "instrumento": 1, "bruto": 1, "moneda": 1, "concertacion": 1},
    ).sort("concertacion", -1).limit(5))
    print("\nMuestra de operaciones de contrapartes (lo que vería la vista):")
    for s in sample:
        print(f"  {s}")
    if not sample:
        print("  (ninguna — confirma que el match está vacío)")


if __name__ == "__main__":
    main()
