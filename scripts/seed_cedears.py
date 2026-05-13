"""seed_cedears.py — upsert de los CEDEARs piloto en Trading.Cedears.

Colección master con metadata categórica (sector, industria, región, país).
Espejo conceptual de Trading.Curvas pero para acciones via CEDEAR.

NO toca market data — eso vive aparte en `Cedears.Snapshot` (DB nueva,
motor dedicado todavía no implementado). Acá solo metadata estática.

Universo piloto: AMD + NVDA. Ambos ARS 24hs. Si el feed pyRofex funciona,
escalamos al universo completo en otro seed.

Uso:
    python -m scripts.seed_cedears               # upsert AMD + NVDA
    python -m scripts.seed_cedears --dry-run     # solo print, no escribe
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from core.mongo import get_mongo_client


# Universo piloto. ratio_cedear lo dejo en None — al momento de escribir
# este seed no tengo confirmación BYMA del ratio vigente para cada uno.
# El user lo completa post-seed o lo levantamos del padrón Rofex en otro
# script.
CEDEARS_PILOTO = [
    {
        "ticker":        "MERV - XMEV - AMD - 24hs",
        "ticker_corto":  "AMD",
        "underlying":    "AMD",
        "ratio_cedear":  None,
        "sector":        "TECNOLOGIA",
        "industria":     "Semiconductores",
        "region":        "US",
        "pais":          "USA",
        "activo":        True,
    },
    {
        "ticker":        "MERV - XMEV - NVDA - 24hs",
        "ticker_corto":  "NVDA",
        "underlying":    "NVDA",
        "ratio_cedear":  None,
        "sector":        "TECNOLOGIA",
        "industria":     "Semiconductores",
        "region":        "US",
        "pais":          "USA",
        "activo":        True,
    },
]


def run(dry_run: bool = False) -> None:
    print("=" * 80)
    print(f"SEED Trading.Cedears — {len(CEDEARS_PILOTO)} docs")
    print(f"Modo: {'DRY-RUN (no escribe)' if dry_run else 'WRITE'}")
    print("=" * 80)

    if dry_run:
        for doc in CEDEARS_PILOTO:
            print(f"\n[DRY] upsert {doc['ticker_corto']}")
            for k, v in doc.items():
                print(f"    {k:<16}: {v}")
        return

    client = get_mongo_client()
    col = client["Trading"]["Cedears"]
    now = datetime.now(timezone.utc)

    for doc in CEDEARS_PILOTO:
        # Upsert idempotente — re-correr no duplica. created_at solo si
        # es nuevo; activo/sector/etc. se pisan en cada run.
        result = col.update_one(
            {"ticker_corto": doc["ticker_corto"]},
            {
                "$set":         doc,
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        if result.upserted_id:
            print(f"  ✓ INSERTED {doc['ticker_corto']:6s} (_id={result.upserted_id})")
        elif result.modified_count:
            print(f"  ↻ UPDATED  {doc['ticker_corto']:6s}")
        else:
            print(f"  · NO-CHANGE {doc['ticker_corto']:6s} (idempotente)")

    total = col.count_documents({})
    print(f"\nTotal docs en Trading.Cedears post-seed: {total}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="No escribe a Mongo, solo print")
    args = ap.parse_args()
    run(dry_run=args.dry_run)
