"""
backfill_assets.py — Script reutilizable para enriquecer Valuaciones.Assets.

Uso:
    /root/TradingAV/venv/bin/python /root/TradingAV/backfill_assets.py
"""

import sys
import os
import re
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


def _extraer_ticker(unidad):
    """Toma todo desde el primer ' - ' hacia la derecha y elimina la palabra FCI."""
    idx = unidad.find(" - ")
    if idx == -1:
        return ""
    ticker = unidad[idx + 3:]
    ticker = re.sub(r'\bFCI\b\s*', '', ticker, flags=re.IGNORECASE).strip()
    return ticker


def rellenar_ticker_fci(col_assets):
    docs = list(col_assets.find({"CARTERA": "CARTERA FCI", "TICKER": ""}))
    print(f"  Docs FCI sin TICKER: {len(docs)}")
    actualizados = 0
    for doc in docs:
        ticker = _extraer_ticker(doc.get("unidad", ""))
        if ticker:
            col_assets.update_one({"_id": doc["_id"]}, {"$set": {"TICKER": ticker}})
            actualizados += 1
    print(f"  TICKER rellenado: {actualizados} docs")


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

    print("\nRellenando TICKER para CARTERA FCI...")
    rellenar_ticker_fci(col_assets)

    client.close()
    print("\nListo.")


if __name__ == "__main__":
    run()
