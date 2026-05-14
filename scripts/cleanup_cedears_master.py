"""cleanup_cedears_master.py — borra entradas obsoletas del master.

Tras los renames (YPF → YPFD, URAC → URA) las viejas entradas quedan
en `Trading.Cedears`. Este script las saca. Después se re-corre el seed
y los nombres nuevos quedan creados.

Uso:
    python -m scripts.cleanup_cedears_master --dry-run
    python -m scripts.cleanup_cedears_master
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client


# Tickers obsoletos a borrar. Los nuevos se crean en seed_cedears.py.
A_BORRAR = ["YPF", "URAC", "DELL", "DIS", "TGS"]


def run(dry_run: bool = False) -> None:
    print("=" * 80)
    print(f"CLEANUP Trading.Cedears — obsoletos: {A_BORRAR}")
    print(f"Modo: {'DRY-RUN' if dry_run else 'DELETE REAL'}")
    print("=" * 80)

    client = get_mongo_client()
    col = client["Trading"]["Cedears"]

    for ticker in A_BORRAR:
        doc = col.find_one({"ticker_corto": ticker})
        if not doc:
            print(f"  · {ticker:<6} no existe — skip")
            continue
        if dry_run:
            print(f"  [DRY] DELETE {ticker:<6} ({doc.get('ticker')})")
        else:
            col.delete_one({"ticker_corto": ticker})
            print(f"  ✓ DELETED {ticker:<6}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(dry_run=args.dry_run)
