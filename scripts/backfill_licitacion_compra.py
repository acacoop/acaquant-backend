"""backfill_licitacion_compra.py — recategoriza boletos op=Licitación que
quedaron como categoria="otro" antes del fix `a06b918`.

Licitación primaria (BYMA / Tesoro) es funcionalmente una compra: entrás
nominales, salís cash. El categorizador actual ya lo mapea a "compra"
(`aunesa_negocio.categorizar`), pero los docs persistidos antes del fix
siguen como "otro" → motor PnL los ignora.

Match: op empieza con "Licitaci" (cubre "Licitación" y "Licitacion",
case-insensitive). Idempotente.

Uso:
    python -m scripts.backfill_licitacion_compra              # ejecuta
    python -m scripts.backfill_licitacion_compra --dry        # solo reporta
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry", action="store_true",
                        help="Solo cuenta cuántos docs cambiarían, no escribe.")
    args = parser.parse_args()

    coll = get_mongo_client()["CashFlow"]["NegocioMovimientos"]

    q = {
        "op": {"$regex": "^[Ll]icitaci", "$options": "i"},
        "categoria": "otro",
    }

    n = coll.count_documents(q)
    print(f"Licitación con categoria='otro': {n}")

    if args.dry:
        print("\n[DRY] no se modificó nada.")
        return 0

    if n == 0:
        print("Nada para backfillear.")
        return 0

    r = coll.update_many(q, {"$set": {"categoria": "compra"}})
    print(f"\nupdated → compra: {r.modified_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
