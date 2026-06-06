"""scripts/diag_contrapartes_gap.py — explica el gap 264(Mongo) vs 249(PG) en contrapartes.

100% LECTURA. CashFlow.Contrapartes es chica (~264 docs) → un scan es trivial, sin
riesgo de CPU. Distingue las 2 causas del gap del sync a Postgres:
  (1) docs sin 'cuenta' (PK del upsert) → se saltean.
  (2) 'cuenta' duplicada → varias contrapartes con el mismo id_cuenta colapsan en una.

    python -m scripts.diag_contrapartes_gap
"""
from __future__ import annotations

from collections import Counter

from core.mongo import get_mongo_client_read


def main() -> int:
    col = get_mongo_client_read()["CashFlow"]["Contrapartes"]
    total = col.count_documents({})
    sin_cuenta = col.count_documents({"$or": [{"cuenta": {"$exists": False}},
                                              {"cuenta": None}, {"cuenta": ""}]})

    cuentas = [d.get("cuenta") for d in col.find({}, {"cuenta": 1})
               if d.get("cuenta") not in (None, "")]
    distintas = len(set(cuentas))
    dups = {c: n for c, n in Counter(cuentas).items() if n > 1}

    print(f"total docs           : {total}")
    print(f"sin 'cuenta'         : {sin_cuenta}")
    print(f"con 'cuenta'         : {len(cuentas)}")
    print(f"id_cuenta distintos  : {distintas}  (= filas esperadas en PG)")
    print(f"valores duplicados   : {len(dups)}")
    if dups:
        print("\n  cuenta duplicada → nombres que la comparten:")
        for c in sorted(dups):
            nombres = [d.get("contraparte") for d in
                       col.find({"cuenta": c}, {"contraparte": 1})]
            print(f"    {c:>8}  x{dups[c]}  {nombres}")
    print(f"\n→ PG debería tener {distintas} filas. Sync reportó 249.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
