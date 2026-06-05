"""Diag READ-ONLY: ¿el filtro es_cierre=False descarta los aranceles de caución?

Las vistas de aranceles filtran es_cierre=False asumiendo que el arancel está
DUPLICADO en apertura+cierre. Pero _negocio_arancelables marca las APERTURAS de
caución como no-arancelables → si Aunesa pone el arancel en el CIERRE
(es_cierre=True), el filtro lo descarta (pérdida pura, no dedup). Esto lo mide.

Lee CashFlow.Operaciones (~487k). Hace 2 pasadas de agregación (one-shot). Si
podés, corrémelo fuera de rueda para no competir con los motores.

Uso:
    python -m scripts.diag_aranceles_caucion
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read


def main() -> int:
    ops = get_mongo_client_read()["CashFlow"]["Operaciones"]

    print("== Arancel (abs) por es_cierre × es_caución  (solo arancel != 0) ==")
    rows = ops.aggregate([
        {"$match": {"arancel": {"$ne": 0, "$exists": True}}},
        {"$group": {
            "_id": {
                "es_cierre": "$es_cierre",
                "caucion": {"$regexMatch": {
                    "input": {"$toLower": {"$ifNull": ["$tipo_operacion", ""]}},
                    "regex": "cauci",
                }},
            },
            "arancel_abs": {"$sum": {"$abs": "$arancel"}},
            "n": {"$sum": 1},
        }},
        {"$sort": {"arancel_abs": -1}},
    ], allowDiskUse=True)
    print(f"  {'caución':<8} {'es_cierre':<10} {'arancel_abs':>16} {'n':>8}")
    for d in rows:
        k = d["_id"]
        print(f"  {k.get('caucion')!s:<8} {k.get('es_cierre')!s:<10} "
              f"{d['arancel_abs']:>16,.2f} {d['n']:>8}")

    print("\n== Cauciones arancelladas: detalle por tipo_operacion × es_cierre ==")
    rows2 = ops.aggregate([
        {"$match": {"tipo_operacion": {"$regex": "cauci", "$options": "i"},
                    "arancel": {"$ne": 0, "$exists": True}}},
        {"$group": {
            "_id": {"tipo": "$tipo_operacion", "es_cierre": "$es_cierre"},
            "arancel_abs": {"$sum": {"$abs": "$arancel"}},
            "n": {"$sum": 1},
        }},
        {"$sort": {"arancel_abs": -1}},
    ], allowDiskUse=True)
    for d in rows2:
        k = d["_id"]
        print(f"  {str(k.get('tipo'))[:44]:<44} es_cierre={k.get('es_cierre')!s:<6} "
              f"arancel_abs={d['arancel_abs']:>12,.2f} n={d['n']}")

    print("\nLectura: si la fila con caución=True y es_cierre=True tiene arancel_abs > 0 "
          "y la de es_cierre=False ≈ 0, el arancel de caución vive en el CIERRE y el "
          "filtro es_cierre=False lo está PERDIENDO (no deduplicando).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
