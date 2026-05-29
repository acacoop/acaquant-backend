"""cleanup_negocio_informacion.py — borra docs ruido de CashFlow.NegocioMovimientos.

Usa la lista canónica de `api/services/_negocio_informacion_filter.py` —
misma fuente que el filtro de ingesta de `aunesa_negocio.py`. Si modificás
los substrings ahí, este script los aplica automáticamente.

Match: `informacion` contiene (substring case-sensitive) cualquier string
de `EXCLUIR_INFORMACION_CONTAINS`.

Idempotente. DRY-RUN por default.

Uso:
    python -m scripts.cleanup_negocio_informacion                    # dry-run, todo el histórico
    python -m scripts.cleanup_negocio_informacion --apply             # borra
    python -m scripts.cleanup_negocio_informacion --desde 2026-01-01  # solo rango
"""
from __future__ import annotations

import argparse
from typing import Any

from api.services._negocio_informacion_filter import (
    EXCLUIR_INFORMACION_CONTAINS,
    match_excluir_informacion,
)
from core.mongo import get_mongo_client

_DB = "CashFlow"
_COL = "NegocioMovimientos"


def main() -> int:
    ap = argparse.ArgumentParser(description="Cleanup de NegocioMovimientos por informacion.")
    ap.add_argument("--desde", default=None, help="restringir a fecha >= YYYY-MM-DD.")
    ap.add_argument("--hasta", default=None, help="restringir a fecha <= YYYY-MM-DD.")
    ap.add_argument("--apply", action="store_true", help="borra. Sin esto, DRY-RUN.")
    args = ap.parse_args()

    col = get_mongo_client()[_DB][_COL]

    match: dict[str, Any] = {**match_excluir_informacion()}
    if args.desde or args.hasta:
        fecha: dict[str, str] = {}
        if args.desde:
            fecha["$gte"] = args.desde
        if args.hasta:
            fecha["$lte"] = args.hasta
        match["fecha"] = fecha

    print("Substrings (case-sensitive) que disparan borrado:")
    for s in EXCLUIR_INFORMACION_CONTAINS:
        print(f"   · {s}")
    print()

    n_total = col.count_documents(match)
    print(f"Docs que matchean: {n_total:,}".replace(",", "."))

    if n_total == 0:
        print("Nada para borrar. Salgo.")
        return 0

    # Desglose por informacion para que se vea qué se va a llevar.
    print("\nBreakdown por `informacion` (top 20):")
    rows = list(col.aggregate([
        {"$match": match},
        {"$group": {"_id": "$informacion", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": 20},
    ]))
    for r in rows:
        val = (r["_id"] or "")[:70]
        print(f"   {r['n']:>10,}".replace(",", ".") + f"   {val}")

    if not args.apply:
        print(f"\n[DRY-RUN] no se borró nada. Re-correr con --apply.")
        return 0

    print(f"\n⚠️  Borrando {n_total:,} docs…".replace(",", "."))
    res = col.delete_many(match)
    print(f"✓ Eliminados: {res.deleted_count:,}".replace(",", "."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
