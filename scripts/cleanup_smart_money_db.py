"""cleanup_smart_money_db.py — drop colecciones Smart Money para liberar
espacio en Atlas.

Las 4 colecciones del módulo Renta Variable Smart Money se van porque la
data acumulada (13F = ~6M docs, Form 4 = decenas de miles) ya no se va a
usar — el módulo Renta Variable está siendo reemplazado por el Scanner
quant con Trading.PreciosAcciones.

Colecciones afectadas:
  Smart.Holdings13F
  Smart.Managers
  Smart.Form4Transactions
  Smart.CEDEARsCatalog

El frontend va a quedar con la tab Smart Money sin data (todo vacío). El
código sigue ahí — se puede revivir cuando se quiera. No se borra código,
solo data.

Uso:
    python -m scripts.cleanup_smart_money_db --dry-run    # solo lista, no borra
    python -m scripts.cleanup_smart_money_db              # drop real
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client


COLECCIONES = ["Holdings13F", "Managers", "Form4Transactions", "CEDEARsCatalog"]


def run(dry_run: bool = False) -> None:
    print("=" * 80)
    print("CLEANUP Smart Money DB")
    print(f"Modo: {'DRY-RUN' if dry_run else 'DROP REAL'}")
    print("=" * 80)

    client = get_mongo_client()
    db = client["Smart"]

    existentes = set(db.list_collection_names())
    total_freed = 0

    for nombre in COLECCIONES:
        if nombre not in existentes:
            print(f"  · Smart.{nombre:<20s} NO EXISTE — skip")
            continue
        try:
            stats = db.command("collStats", nombre)
            size_mb = stats.get("size", 0) / 1024 / 1024
            count = stats.get("count", 0)
        except Exception as e:
            size_mb = 0
            count = 0
            print(f"  ⚠ collStats falló para {nombre}: {e}")
        total_freed += size_mb
        if dry_run:
            print(f"  [DRY] DROP Smart.{nombre:<20s} ({count:,} docs · {size_mb:.1f} MB)")
        else:
            db[nombre].drop()
            print(f"  ✓ DROPPED Smart.{nombre:<20s} ({count:,} docs · {size_mb:.1f} MB)")

    print(f"\nTotal espacio {'a liberar' if dry_run else 'liberado'}: {total_freed:.1f} MB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(dry_run=args.dry_run)
