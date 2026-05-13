"""seed_cedears.py — upsert de CEDEARs en Trading.Cedears.

Colección master con metadata categórica (sector, industria, región, país).
Espejo conceptual de Trading.Curvas pero para acciones via CEDEAR.

NO toca market data — eso vive aparte en `Trading.CedearsSnapshot` (escrito
por `engines/motor_cedears.py` cada 1s).

LAR y URAC quedan con sector=OTROS / industria=TODO porque no los puedo
identificar con certeza desde acá. Editá el doc en Mongo cuando los
confirmes para que aparezcan bien categorizados en el scanner.

Uso:
    python -m scripts.seed_cedears               # upsert todos
    python -m scripts.seed_cedears --dry-run     # solo print, no escribe
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from core.mongo import get_mongo_client


def _cedear(ticker_corto: str, sector: str, industria: str, region: str, pais: str) -> dict:
    return {
        "ticker":       f"MERV - XMEV - {ticker_corto} - 24hs",
        "ticker_corto": ticker_corto,
        "underlying":   ticker_corto,
        "ratio_cedear": None,  # vos lo completás post-seed
        "sector":       sector,
        "industria":    industria,
        "region":       region,
        "pais":         pais,
        "activo":       True,
    }


CEDEARS = [
    # Semiconductores (US + Asia)
    _cedear("AMD",   "TECNOLOGIA",          "Semiconductores", "US",    "USA"),
    _cedear("NVDA",  "TECNOLOGIA",          "Semiconductores", "US",    "USA"),
    _cedear("INTC",  "TECNOLOGIA",          "Semiconductores", "US",    "USA"),
    _cedear("TSM",   "TECNOLOGIA",          "Semiconductores", "ASIA",  "Taiwan"),

    # Software / Internet / Hardware (US mega-caps)
    _cedear("AAPL",  "TECNOLOGIA",          "Hardware",        "US",    "USA"),
    _cedear("MSFT",  "TECNOLOGIA",          "Software",        "US",    "USA"),
    _cedear("GOOGL", "TECNOLOGIA",          "Internet",        "US",    "USA"),
    _cedear("META",  "TECNOLOGIA",          "Internet",        "US",    "USA"),
    _cedear("AMZN",  "TECNOLOGIA",          "E-commerce",      "US",    "USA"),
    _cedear("ORCL",  "TECNOLOGIA",          "Software",        "US",    "USA"),
    _cedear("PLTR",  "TECNOLOGIA",          "Software",        "US",    "USA"),
    _cedear("DELL",  "TECNOLOGIA",          "Hardware",        "US",    "USA"),

    # Comunicaciones / streaming
    _cedear("NFLX",  "COMUNICACIONES",      "Streaming",       "US",    "USA"),

    # Consumo
    _cedear("KO",    "CONSUMO BASICO",      "Bebidas",         "US",    "USA"),
    _cedear("BABA",  "CONSUMO DISCRECIONAL","E-commerce",      "ASIA",  "China"),

    # Industriales
    _cedear("RKLB",  "INDUSTRIALES",        "Aeroespacial",    "US",    "USA"),

    # Energía (Argentina + Brasil)
    _cedear("YPF",   "ENERGIA",             "Oil & Gas",       "LATAM", "Argentina"),
    _cedear("VIST",  "ENERGIA",             "Oil & Gas",       "LATAM", "Argentina"),
    _cedear("PBR",   "ENERGIA",             "Oil & Gas",       "LATAM", "Brasil"),

    # Financiero
    _cedear("GGAL",  "FINANCIERO",          "Bancos",          "LATAM", "Argentina"),

    # ETFs (índices + cripto)
    _cedear("SPY",   "ETF",                 "S&P 500",         "US",    "USA"),
    _cedear("QQQ",   "ETF",                 "Nasdaq 100",      "US",    "USA"),
    _cedear("IWM",   "ETF",                 "Russell 2000",    "US",    "USA"),
    _cedear("IBIT",  "ETF",                 "Bitcoin",         "US",    "USA"),
    _cedear("ETHA",  "ETF",                 "Ethereum",        "US",    "USA"),

    # Pendientes de identificar — TODO: confirmar
    _cedear("LAR",   "OTROS",               "TODO",            "TODO",  "TODO"),
    _cedear("URAC",  "OTROS",               "TODO",            "TODO",  "TODO"),

    # Migrados desde la watchlist (sección "Acciones" que se elimina, todo
    # se concentra en el Scanner). 2026-05-13.
    _cedear("TSLA",  "CONSUMO DISCRECIONAL", "Autos",          "US",    "USA"),
    _cedear("JPM",   "FINANCIERO",           "Bancos",         "US",    "USA"),
    _cedear("MELI",  "TECNOLOGIA",           "E-commerce",     "LATAM", "Argentina"),
]


def run(dry_run: bool = False) -> None:
    print("=" * 80)
    print(f"SEED Trading.Cedears — {len(CEDEARS)} docs")
    print(f"Modo: {'DRY-RUN (no escribe)' if dry_run else 'WRITE'}")
    print("=" * 80)

    if dry_run:
        for doc in CEDEARS:
            print(f"\n[DRY] upsert {doc['ticker_corto']:6s} ({doc['sector']} / {doc['industria']} / {doc['pais']})")
        return

    client = get_mongo_client()
    col = client["Trading"]["Cedears"]
    now = datetime.now(timezone.utc)

    inserted = updated = unchanged = 0
    for doc in CEDEARS:
        result = col.update_one(
            {"ticker_corto": doc["ticker_corto"]},
            {
                "$set":         doc,
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        if result.upserted_id:
            print(f"  + INSERTED {doc['ticker_corto']:6s}")
            inserted += 1
        elif result.modified_count:
            print(f"  ~ UPDATED  {doc['ticker_corto']:6s}")
            updated += 1
        else:
            unchanged += 1

    total = col.count_documents({})
    print(f"\nResumen: {inserted} insert · {updated} update · {unchanged} sin cambios")
    print(f"Total docs en Trading.Cedears: {total}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="No escribe a Mongo, solo print")
    args = ap.parse_args()
    run(dry_run=args.dry_run)
