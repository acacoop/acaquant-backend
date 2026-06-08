"""scripts/diag_contrapartes.py — qué hay REALMENTE en CashFlow.Contrapartes
(read-only, REGLA #2). Para entender por qué el dropdown de SEGMENTO de la vista
trae demasiadas opciones y por qué la lista puede venir vacía.

Mide: total de docs, cuántos sin `cuenta`, distinct de `segmento` (con conteo —
acá se ve si está sucio con nombres de fondos), distinct de `contraparte` (las
keywords del conciliador) y unos docs de muestra.

Uso (en el Droplet):
    python -m scripts.diag_contrapartes
"""
from __future__ import annotations

from collections import Counter

from core.mongo import get_mongo_client_read


def main() -> int:
    col = get_mongo_client_read()["CashFlow"]["Contrapartes"]

    total = col.count_documents({})
    sin_cuenta = col.count_documents({"cuenta": {"$in": [None, ""]}})
    print(f"Total docs Contrapartes: {total:,}")
    print(f"  sin `cuenta` (null/''): {sin_cuenta:,}")

    print("\n── distinct(segmento) con conteo (lo que puebla el dropdown) ──")
    segs = Counter()
    for d in col.find({}, {"_id": 0, "segmento": 1}):
        segs[d.get("segmento")] += 1
    print(f"  {len(segs)} valores distintos de `segmento`:")
    for val, n in segs.most_common(40):
        print(f"    {n:>5}  {val!r}")
    if len(segs) > 40:
        print(f"    … y {len(segs) - 40} más")

    print("\n── distinct(contraparte) — keywords del conciliador ──")
    cps = [c for c in col.distinct("contraparte") if c]
    print(f"  {len(cps)} contrapartes distintas. Muestra (primeras 25):")
    for c in sorted(cps)[:25]:
        print(f"    {c!r}")

    print("\n── 5 docs de muestra ──")
    for d in col.find({}, {"_id": 0, "cuenta": 1, "denominacion": 1,
                           "contraparte": 1, "segmento": 1}).limit(5):
        print(f"    {d}")

    print("\nLISTO (read-only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
