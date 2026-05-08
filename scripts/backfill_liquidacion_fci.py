"""backfill_liquidacion_fci.py — recategoriza boletos cuya `informacion`
es "Liquidación de suscripción/rescate - [CAFCI...] cant@precio" pero
quedaron como categoria="otro" con ticker=null porque el parser no los
contemplaba (faltaba `PATTERN_LIQUIDACION_FCI`).

Re-corre el parser nuevo sobre los docs persistidos y completa:
  - ticker        ← parsed.ticker
  - op            ← "Liquidación de suscripción"|"Liquidación de rescate"
  - categoria     ← suscripcion_fci | rescate_fci (vía categorizar())
  - precio        ← parsed.precio (si está null o 0)

NO toca `cantidad`, `importe`, `moneda` — ya vienen consolidados de las
líneas del comprobante (sum de _total_cliente), independientes del
parser de informacion.

Idempotente: si el doc ya tiene ticker no-null y categoria !=otro,
no lo toca.

Uso:
    python -m scripts.backfill_liquidacion_fci              # ejecuta
    python -m scripts.backfill_liquidacion_fci --dry        # solo reporta
"""
from __future__ import annotations

import argparse

from api.services.aunesa_negocio import categorizar, parse_informacion
from core.mongo import get_mongo_client


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry", action="store_true",
                        help="Solo cuenta cuántos docs cambiarían, no escribe.")
    args = parser.parse_args()

    coll = get_mongo_client()["CashFlow"]["NegocioMovimientos"]

    # Match: informacion empieza con "Liquidación de suscripción/rescate"
    # (con o sin tilde, case-insensitive). Filtramos los que están como
    # "otro" o sin ticker — los ya recategorizados no se re-procesan.
    q = {
        "informacion": {
            "$regex": r"^Liquidaci[oó]n\s+de\s+(suscripci[oó]n|rescate)",
            "$options": "i",
        },
        "$or": [
            {"categoria": "otro"},
            {"ticker": None},
        ],
    }

    docs = list(coll.find(q, {"_id": 1, "informacion": 1, "ticker": 1,
                              "categoria": 1, "op": 1, "precio": 1}))
    print(f"Liquidación FCI sin recategorizar: {len(docs)}")

    n_parsed = 0
    n_skip = 0
    actualizar: list[tuple] = []
    for d in docs:
        parsed = parse_informacion(d.get("informacion") or "")
        if not parsed:
            n_skip += 1
            continue
        categoria = categorizar(d.get("informacion") or "", parsed)
        nuevo = {
            "ticker":    parsed.get("ticker"),
            "op":        parsed.get("op"),
            "categoria": categoria,
        }
        # `precio` solo si el doc no tiene uno válido.
        precio_actual = d.get("precio") or 0
        if not precio_actual and parsed.get("precio"):
            nuevo["precio"] = parsed["precio"]
        actualizar.append((d["_id"], nuevo))
        n_parsed += 1

    print(f"Parseables (van a actualizar): {n_parsed}")
    print(f"No parseables (skip):          {n_skip}")

    if args.dry:
        print("\n[DRY] no se modificó nada.")
        if actualizar[:3]:
            print("\nMuestra de los primeros 3:")
            for _id, nuevo in actualizar[:3]:
                print(f"  {_id} → {nuevo}")
        return 0

    if not actualizar:
        print("\nNada para backfillear.")
        return 0

    n_updated = 0
    for _id, nuevo in actualizar:
        coll.update_one({"_id": _id}, {"$set": nuevo})
        n_updated += 1

    print(f"\nupdated → {n_updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
