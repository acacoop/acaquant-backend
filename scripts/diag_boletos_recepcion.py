"""
diag_boletos_recepcion.py — listar boletos de "Recepción" en
CashFlow.NegocioMovimientos para diagnosticar cómo categorizarlos
en el motor PnL.

Hoy estos boletos quedan en `categoria='otro'` y el motor los ignora,
inflando pnl_no_realizado en PNL TÍTULOS porque qty_aum sube pero
costo_remanente no.

Read-only. No toca nada. Reporta:
  - Total de boletos que matchean.
  - Agrupado por `op` distinto (count + categoria actual).
  - 2 samples por cada op con todos los campos relevantes.

Uso:
    python -m scripts.diag_boletos_recepcion
"""
from __future__ import annotations

from core.mongo import get_mongo_client

# Captura "Recepción", "Recepcion" (sin tilde), "recepci..." en op.
# El $regex es case-insensitive para no perder variantes.
_PATTERN = "recepci"


def main():
    client = get_mongo_client()
    coll = client["CashFlow"]["NegocioMovimientos"]

    total = coll.count_documents({"op": {"$regex": _PATTERN, "$options": "i"}})
    print(f"\nTotal boletos con op matchea /{_PATTERN}/i: {total}")

    if total == 0:
        return

    # Agrupar por op distinto + categoria actual.
    pipeline = [
        {"$match": {"op": {"$regex": _PATTERN, "$options": "i"}}},
        {"$group": {
            "_id": {"op": "$op", "categoria": "$categoria"},
            "n":   {"$sum": 1},
        }},
        {"$sort": {"_id.op": 1, "_id.categoria": 1}},
    ]
    grupos = list(coll.aggregate(pipeline, allowDiskUse=True))

    # Re-agrupar por op para mostrar mejor.
    por_op: dict[str, list[tuple[str, int]]] = {}
    for g in grupos:
        op = g["_id"]["op"]
        cat = g["_id"].get("categoria") or "<null>"
        n = g["n"]
        por_op.setdefault(op, []).append((cat, n))

    print(f"\n{len(por_op)} op distintos:\n")

    for op, cats in por_op.items():
        total_op = sum(n for _, n in cats)
        cat_str = ", ".join(f"{cat}={n}" for cat, n in cats)
        print(f"  ── op={op!r}")
        print(f"     count={total_op}  ({cat_str})")

        # 2 samples para inspección.
        samples = list(coll.find(
            {"op": op},
            {"_id": 0, "fecha": 1, "cantidad": 1, "precio": 1, "importe": 1,
             "moneda": 1, "ticker": 1, "categoria": 1, "informacion": 1,
             "cuenta": 1, "comprobante": 1, "mep": 1},
        ).limit(2))
        for s in samples:
            print("     sample:")
            print(f"        fecha={s.get('fecha')}  ticker={s.get('ticker')}  "
                  f"cuenta={s.get('cuenta')}")
            print(f"        cantidad={s.get('cantidad')}  precio={s.get('precio')}  "
                  f"importe={s.get('importe')}  moneda={s.get('moneda')}  "
                  f"mep={s.get('mep')}")
            print(f"        categoria={s.get('categoria')!r}")
            print(f"        informacion={s.get('informacion')!r}")
        print()


if __name__ == "__main__":
    main()
