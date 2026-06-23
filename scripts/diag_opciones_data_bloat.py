"""diag_opciones_data_bloat.py — cuánto de Opciones.Data es de vencimientos VIEJOS.

Read-only. El 1M de Opciones.Data acumula ticks de opciones de vencimientos pasados
(junio cuando ya es agosto). Los symbols VIGENTES están en Opciones.OptionsSnapshot (el
motor la purga). Esto agrupa Data por symbol y marca cuáles ya no son vigentes → el conteo
de "viejos" es lo que un cleanup borraría. Correr FUERA de rueda (es un scan de Data).

    python -m scripts.diag_opciones_data_bloat
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read


def main() -> int:
    opc = get_mongo_client_read()["Opciones"]
    vigentes = {d["symbol"] for d in opc["OptionsSnapshot"].find({}, {"_id": 0, "symbol": 1})
                if d.get("symbol")}
    total = opc["Data"].estimated_document_count()
    print(f"Opciones.Data: ~{total:,} docs · OptionsSnapshot (vigentes): {len(vigentes)} symbols\n")

    rows = list(opc["Data"].aggregate([
        {"$group": {"_id": "$symbol", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]))
    n_vig = sum(r["n"] for r in rows if r["_id"] in vigentes)
    n_viejos = sum(r["n"] for r in rows if r["_id"] not in vigentes)
    syms_viejos = sum(1 for r in rows if r["_id"] not in vigentes)

    print(f"Top 25 symbols por # de ticks (de {len(rows)} symbols distintos):")
    for r in rows[:25]:
        es_vig = r["_id"] in vigentes
        print(f"  {r['_id']!s:<30} {r['n']:>9,}  {'VIGENTE' if es_vig else '⚠ VIEJO'}")

    print("\n" + "─" * 55)
    print(f"Ticks de symbols VIGENTES:  {n_vig:>10,}  ({len(vigentes & {r['_id'] for r in rows})} symbols)")
    print(f"Ticks de symbols VIEJOS:    {n_viejos:>10,}  ({syms_viejos} symbols)  ← BORRABLE")
    pct = (n_viejos / total * 100) if total else 0
    print(f"\n→ el cleanup liberaría ~{pct:.0f}% de Data ({n_viejos:,} docs).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
