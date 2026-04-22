"""Imprime el estado actual de Trading.MarketSnapshot para inspección.

Uso:
    python -m scripts.check_snapshot                # todos los soberanos
    python -m scripts.check_snapshot GD29D GD41D    # subset
    python -m scripts.check_snapshot --all          # todos los tickers de Curvas
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tickers", nargs="*")
    parser.add_argument("--all", action="store_true",
                        help="Todos los tickers de Trading.Curvas")
    args = parser.parse_args()

    client = get_mongo_client()
    curvas = client["Trading"]["Curvas"]
    snap = client["Trading"]["MarketSnapshot"]

    if args.all:
        docs_curvas = list(curvas.find({}, {"_id": 0, "ticker": 1, "ticker_corto": 1, "curva": 1}))
    elif args.tickers:
        docs_curvas = list(curvas.find(
            {"ticker_corto": {"$in": args.tickers}},
            {"_id": 0, "ticker": 1, "ticker_corto": 1, "curva": 1},
        ))
    else:
        docs_curvas = list(curvas.find(
            {"curva": "soberanos"},
            {"_id": 0, "ticker": 1, "ticker_corto": 1, "curva": 1},
        ))

    if not docs_curvas:
        print("Sin tickers que chequear.")
        return 1

    print(f"{'Ticker Corto':<12} {'Last':>10} {'Bid':>10} {'Offer':>10} "
          f"{'TEA':>8} {'Dur':>8} {'Pari':>8}   Snapshot en Mongo")
    print("-" * 100)

    for c in sorted(docs_curvas, key=lambda x: x.get("ticker_corto") or ""):
        ticker = c.get("ticker")
        tc = c.get("ticker_corto")
        s = snap.find_one({"ticker": ticker}, {"_id": 0})
        if not s:
            print(f"{tc:<12} {'—':>10} {'—':>10} {'—':>10} {'—':>8} {'—':>8} {'—':>8}   NO EXISTE")
            continue
        m = s.get("metrics") or {}
        book = s.get("book") or {}
        bids = book.get("bids") or []
        offers = book.get("offers") or []

        last = m.get("last_price")
        bid = bids[0].get("price") if bids else None
        offer = offers[0].get("price") if offers else None
        tea = m.get("TEA")
        dur = m.get("duration")
        pari = m.get("paridad")

        print(
            f"{tc:<12} "
            f"{last!s:>10} "
            f"{bid!s:>10} "
            f"{offer!s:>10} "
            f"{(f'{tea*100:.2f}%' if tea else '—'):>8} "
            f"{(f'{dur:.2f}' if dur else '—'):>8} "
            f"{(f'{pari:.1f}' if pari else '—'):>8}   "
            f"exists"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
