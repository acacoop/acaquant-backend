"""drop_aumapi.py — dropea PortfolioAPI.AumAPI (espejo ya sin consumidores).

Correr DESPUÉS de deployar la API nueva y confirmar que las vistas de AuM
(AUM, valuaciones, tasa fija, CER, flujo-vs-aum) traen datos. La API ya lee
Valuaciones.AuM directo. Al quedar PortfolioAPI sin colecciones, Mongo la elimina.

IRREVERSIBLE → default --dry-run. Dropea con --apply. Idempotente.
Se borra tras confirmar el drop (REGLA #5).

    python -m scripts.drop_aumapi            # dry-run
    python -m scripts.drop_aumapi --apply    # dropea
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="dropea de verdad (sin esto: dry-run)")
    args = ap.parse_args()

    db = get_mongo_client()["PortfolioAPI"]
    if "AumAPI" not in db.list_collection_names():
        print("PortfolioAPI.AumAPI no existe (ya borrada) — nada que hacer.")
        return 0
    n = db["AumAPI"].estimated_document_count()
    if args.apply:
        db["AumAPI"].drop()
        print(f"✔ PortfolioAPI.AumAPI DROPEADA ({n:,} docs)")
    else:
        print(f"[DRY-RUN] PortfolioAPI.AumAPI: {n:,} docs — se dropearía. Corré con --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
