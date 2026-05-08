"""backfill_trd_categorias.py — recategoriza boletos op=TRD que quedaron
como categoria="otro" porque el categorizador viejo no contemplaba TRD.

TRD es el op genérico de trading (similar a licitación, sin texto "Compra"
o "Venta" en `informacion`). Se decide compra/venta por signo del importe
(perspectiva cliente, ya invertido en aunesa_negocio._enriquecer):
  - importe < 0 → compra (pagamos cash)
  - importe > 0 → venta (recibimos cash)

Idempotente: si la categoria ya está bien, no toca nada.

Uso:
    python -m scripts.backfill_trd_categorias              # ejecuta
    python -m scripts.backfill_trd_categorias --dry        # solo reporta
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

    q_compra = {"op": "TRD", "categoria": "otro", "importe": {"$lt": 0}}
    q_venta  = {"op": "TRD", "categoria": "otro", "importe": {"$gt": 0}}

    n_compra = coll.count_documents(q_compra)
    n_venta  = coll.count_documents(q_venta)
    n_zero   = coll.count_documents({"op": "TRD", "categoria": "otro", "importe": 0})

    print(f"TRD compra (importe<0): {n_compra}")
    print(f"TRD venta  (importe>0): {n_venta}")
    print(f"TRD importe=0 (skip):   {n_zero}")

    if args.dry:
        print("\n[DRY] no se modificó nada.")
        return 0

    r1 = coll.update_many(q_compra, {"$set": {"categoria": "compra"}})
    r2 = coll.update_many(q_venta,  {"$set": {"categoria": "venta"}})
    print(f"\nupdated → compra: {r1.modified_count} · venta: {r2.modified_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
