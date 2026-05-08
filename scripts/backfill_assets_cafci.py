"""backfill_assets_cafci.py — auto-fill del campo CAFCI en
Valuaciones.Assets para los docs ya existentes.

CAFCI es derivado de `unidad` (regex `\\bCAFCI\\d+-\\d+\\b`). El cron
(`jobs/aum.py::_sincronizar_assets`) lo mantiene en sync para upserts
nuevos. Este script cubre los docs persistidos antes del fix.

Idempotente: solo escribe cuando el valor calculado difiere del actual.

Uso:
    python -m scripts.backfill_assets_cafci              # ejecuta
    python -m scripts.backfill_assets_cafci --dry        # solo reporta
"""
from __future__ import annotations

import argparse

from core.cafci import extract_cafci
from core.mongo import get_mongo_client


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry", action="store_true",
                        help="Solo cuenta cuántos docs cambiarían, no escribe.")
    args = parser.parse_args()

    coll = get_mongo_client()["Valuaciones"]["Assets"]

    n_total = 0
    n_with_cafci = 0
    n_to_set = 0
    n_already_ok = 0

    docs = coll.find({}, {"_id": 1, "unidad": 1, "CAFCI": 1})
    pendientes: list[tuple] = []

    for d in docs:
        n_total += 1
        unidad = d.get("unidad") or ""
        cafci = extract_cafci(unidad)
        if cafci:
            n_with_cafci += 1
        actual = (d.get("CAFCI") or "").strip() or None
        if actual == cafci:
            n_already_ok += 1
            continue
        n_to_set += 1
        pendientes.append((d["_id"], cafci))

    print(f"Total Assets:                  {n_total}")
    print(f"Con CAFCI en unidad:           {n_with_cafci}")
    print(f"Ya tienen CAFCI correcto:      {n_already_ok}")
    print(f"Pendientes de actualizar:      {n_to_set}")

    if args.dry:
        print("\n[DRY] no se modificó nada.")
        return 0

    if not pendientes:
        print("\nNada para backfillear.")
        return 0

    n_updated = 0
    for _id, cafci in pendientes:
        coll.update_one({"_id": _id}, {"$set": {"CAFCI": cafci}})
        n_updated += 1

    print(f"\nupdated → {n_updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
