"""migrate_ons_tickers_curvas.py — backfill del par `tickers` en las ONs de Curvas.

Fase 3 del decommission de BondsMaster (consolidar en Trading.Curvas). Los docs on_*
ya están en Curvas (flujos/emisor/sector/etc.), pero les falta el campo `tickers`
{ARS, USD} (las 2 patas) que vivía en BondsMaster. Esto lo copia BondsMaster → Curvas
para que el editor muestre ambas patas al editar una ON existente.

Scopeado (solo las 169 ONs), idempotente, dry-run por default (REGLA #4).

    python -m scripts.migrate_ons_tickers_curvas            # dry-run
    python -m scripts.migrate_ons_tickers_curvas --apply    # escribe
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="escribir (default: dry-run)")
    args = ap.parse_args()

    cli = get_mongo_client()
    bm = list(cli["Trading"]["BondsMaster"].find({}, {"_id": 0, "asset": 1, "tickers": 1}))
    curvas = cli["Trading"]["Curvas"]

    n_set, n_skip, n_nocurva = 0, 0, 0
    for b in bm:
        asset = b.get("asset")
        tickers = b.get("tickers") or {}
        if not asset or not (tickers.get("ARS") or tickers.get("USD")):
            n_skip += 1
            continue
        c = curvas.find_one({"ticker_corto": asset, "curva": {"$regex": "^on"}},
                            {"_id": 0, "tickers": 1})
        if not c:
            n_nocurva += 1
            continue
        if c.get("tickers"):
            n_skip += 1   # ya tiene → idempotente
            continue
        payload = {"tickers": {"ARS": tickers.get("ARS"), "USD": tickers.get("USD")}}
        if args.apply:
            curvas.update_one({"ticker_corto": asset, "curva": {"$regex": "^on"}},
                              {"$set": payload})
        n_set += 1

    print(f"BondsMaster: {len(bm)} docs")
    print(f"{'SET' if args.apply else '[dry] setearía'} tickers en Curvas: {n_set}")
    print(f"ya tenían / sin tickers: {n_skip}   ·   sin doc on_* en Curvas: {n_nocurva}")
    if not args.apply:
        print("\n(DRY-RUN — nada escrito. Correr con --apply.)")
    else:
        print("\n✅ Backfill aplicado. El editor de ONs ya muestra las 2 patas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
