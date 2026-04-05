"""
backfill_assets.py — Script reutilizable para enriquecer Valuaciones.Assets.

Uso:
    /root/TradingAV/venv/bin/python /root/TradingAV/backfill_assets.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from mongo_manager import get_mongo_client

# Reglas: (keyword_regex, EMISOR_valor)
# Solo aplica a docs con CARTERA = "CARTERA FCI" y EMISOR = ""
REGLAS_EMISOR_FCI = [
    ("Max",         "MAX"),
    ("Schroder",    "SCHRODER"),
    ("Toronto",     "TORONTO"),
    ("BAVSA",       "BAVSA"),
    ("IAM",         "IAM"),
    ("Allaria",     "ALLARIA"),
    ("SBS",         "SBS"),
    ("Consultatio", "one618"),
    ("ST",          "one618"),
    ("First",       "FIRST"),
    ("Balanz",      "BALANZ"),
    ("Compass",     "COMPASS"),
    ("ConoSur",     "ConoSUR"),
    ("ADCAP",       "ADCAP"),
    ("IEB",         "IEB"),
    ("Lombard",     "LOMBARD"),
    ("megaqm",      "MEGAQM"),
    ("Bull Market", "BULL MARKET"),
    ("BULLMARKET",  "BULL MARKET"),
]


def rellenar_vencimiento_fci(col_assets):
    result = col_assets.update_many(
        {"CARTERA": "CARTERA FCI", "VENCIMIENTO": ""},
        {"$set": {"VENCIMIENTO": "NO APLICA"}},
    )
    print(f"  VENCIMIENTO → NO APLICA: {result.modified_count} docs")


def rellenar_emisor_fci(col_assets):
    print("Rellenando EMISOR para CARTERA FCI...")
    total = 0
    for keyword, emisor in REGLAS_EMISOR_FCI:
        result = col_assets.update_many(
            {
                "CARTERA": "CARTERA FCI",
                "EMISOR":  "",
                "unidad":  {"$regex": keyword, "$options": "i"},
            },
            {"$set": {"EMISOR": emisor}},
        )
        if result.modified_count:
            print(f"  {keyword:15s} → {emisor:10s}: {result.modified_count} docs")
        total += result.modified_count

    sin_emisor = col_assets.count_documents({"CARTERA": "CARTERA FCI", "EMISOR": ""})
    print(f"\n  Total actualizados: {total}")
    print(f"  FCI sin EMISOR aún: {sin_emisor}")


def run():
    client = get_mongo_client()
    col_assets = client["Valuaciones"]["Assets"]

    print("Rellenando VENCIMIENTO para CARTERA FCI...")
    rellenar_vencimiento_fci(col_assets)

    print("\nRellenando EMISOR para CARTERA FCI...")
    rellenar_emisor_fci(col_assets)

    client.close()
    print("\nListo.")


if __name__ == "__main__":
    run()
