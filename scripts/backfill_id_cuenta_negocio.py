"""scripts/backfill_id_cuenta_negocio.py — rellena id_cuenta en NegocioMovimientos.

Denormaliza `id_cuenta` (extraído de `cuenta` = "[805] NOMBRE") en los docs
existentes, para que la vista COMERCIAL filtre por índice en vez de regex.
Server-side (un solo update_many con pipeline) → no trae docs al cliente.
Idempotente: solo toca los que NO tienen id_cuenta. Crea también los índices.

Uso (en el Droplet, tras git pull):
    python -m scripts.backfill_id_cuenta_negocio            # ejecuta
    python -m scripts.backfill_id_cuenta_negocio --dry-run  # solo cuenta
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

DB_NAME = "CashFlow"
COL_NAME = "NegocioMovimientos"

# $set id_cuenta = primer grupo de captura de ^\[(\d+)\] sobre cuenta; null si no matchea.
_PIPELINE = [
    {"$set": {
        "id_cuenta": {
            "$let": {
                "vars": {"m": {"$regexFind": {"input": "$cuenta", "regex": r"^\[(\d+)\]"}}},
                "in": {
                    "$cond": [
                        {"$gt": [{"$size": {"$ifNull": ["$$m.captures", []]}}, 0]},
                        {"$arrayElemAt": ["$$m.captures", 0]},
                        None,
                    ],
                },
            },
        },
    }},
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="solo cuenta, no escribe")
    args = ap.parse_args()

    coll = get_mongo_client()[DB_NAME][COL_NAME]
    falta = coll.count_documents({"id_cuenta": {"$exists": False}})
    total = coll.estimated_document_count()
    print(f"NegocioMovimientos: {total} docs · sin id_cuenta: {falta}")

    if args.dry_run:
        print("(dry-run) no se escribe nada.")
        return

    if falta:
        res = coll.update_many({"id_cuenta": {"$exists": False}}, _PIPELINE)
        print(f"✔ id_cuenta backfilleado en {res.modified_count} docs.")
    else:
        print("Nada que backfillear.")

    # Índices (idempotente) — mismos que crea el job en cada corrida.
    coll.create_index([("id_cuenta", 1), ("fecha", -1)], name="idcuenta_fecha")
    coll.create_index(
        [("id_cuenta", 1), ("categoria", 1), ("fecha", -1)],
        name="idcuenta_categoria_fecha",
    )
    print("✔ índices id_cuenta listos.")

    sin_match = coll.count_documents({"id_cuenta": None})
    print(f"docs con cuenta sin formato [id] (id_cuenta=null): {sin_match}")


if __name__ == "__main__":
    main()
