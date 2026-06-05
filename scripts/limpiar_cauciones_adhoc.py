"""Borra las suscripciones de CAUCIÓN de Trading.AdhocSubscriptions.

Las cauciones (PESOS/DOLAR a plazo Nd, ej. 'MERV - XMEV - PESOS - 1D') se
registraban como adhoc desde el quick-pick del Dashboard de Operar. Varias NO
existen en ROFEX → el motor_rofex rechazaba la suscripción ENTERA y entraba en
loop de reconexión → order book vacío para TODOS los tickers (incidente
2026-06-05). Se sacó el quick-pick del front; este script limpia las que
quedaron registradas para que el `_adhoc_watcher` deje de re-suscribirlas
(las re-mete cada 5s mientras sigan en la colección).

Scopeado: SOLO borra tickers que matchean `(PESOS|DOLAR) - <N>D` (formato de
plazo de caución). No toca ningún otro adhoc. Idempotente.

Uso:
    python -m scripts.limpiar_cauciones_adhoc            # borra (default)
    python -m scripts.limpiar_cauciones_adhoc --dry-run  # solo lista, no borra
"""
from __future__ import annotations

import argparse
import re

from core.mongo import get_mongo_client

# Plazo de caución: '... - PESOS - 1D' / '... - DOLAR - 7D'. Los tickers
# normales terminan en CI / 24hs / 48hs, así que esto no los toca.
_CAUCION_RE = re.compile(r"-\s*(PESOS|DOLAR)\s*-\s*\d+D\s*$", re.I)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Solo lista lo que borraría; no borra nada.")
    args = ap.parse_args()

    col = get_mongo_client()["Trading"]["AdhocSubscriptions"]
    docs = list(col.find({}, {"_id": 1, "ticker": 1}))
    objetivo = [
        d for d in docs
        if _CAUCION_RE.search(str(d.get("ticker") or d.get("_id") or ""))
    ]

    print(f"AdhocSubscriptions: {len(docs)} activas · {len(objetivo)} de caución a borrar.")
    for d in objetivo:
        print(f"  - {d.get('ticker') or d.get('_id')}")

    if not objetivo:
        print("Nada que borrar.")
        return 0
    if args.dry_run:
        print("[dry-run] no se borró nada.")
        return 0

    res = col.delete_many({"_id": {"$in": [d["_id"] for d in objetivo]}})
    print(f"Borradas {res.deleted_count} suscripciones de caución.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
