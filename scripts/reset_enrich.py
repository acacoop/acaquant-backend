"""Resetea los campos enriquecidos (TEA/TEM/duration/convexity/paridad) de
todos los trades de un ticker en Trading.TimeSales.

Cuando se cambia la lógica del motor_curvas, los docs previos ya
enriquecidos quedan con valores viejos porque el engine solo procesa
docs SIN `duration`. Este script elimina esos campos para que el motor
los re-procese en el próximo ciclo (dentro de 5s).

Uso:
    python -m scripts.reset_enrich "MERV - XMEV - GD30D - 24hs"
    python -m scripts.reset_enrich GD30D             # acepta ticker_corto
"""
from __future__ import annotations

import sys

from core.mongo import get_mongo_client


def _resolver_ticker(ticker_or_short: str) -> str:
    """Si el input no trae ' - ', asume ticker_corto y lo resuelve via Curvas."""
    if " - " in ticker_or_short:
        return ticker_or_short
    client = get_mongo_client()
    doc = client["Trading"]["Curvas"].find_one(
        {"ticker_corto": ticker_or_short}, {"ticker": 1, "_id": 0},
    )
    if not doc or not doc.get("ticker"):
        raise SystemExit(
            f"✗ No encuentro ticker_corto='{ticker_or_short}' en Trading.Curvas"
        )
    return doc["ticker"]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    ticker = _resolver_ticker(sys.argv[1])
    client = get_mongo_client()
    col = client["Trading"]["TimeSales"]

    antes = col.count_documents({"ticker": ticker})
    con_enrich = col.count_documents({"ticker": ticker, "duration": {"$exists": True}})

    print(f"Ticker:            {ticker}")
    print(f"Docs totales:      {antes}")
    print(f"Con enrichment:    {con_enrich}")

    if con_enrich == 0:
        print("Nada para resetear.")
        return 0

    res = col.update_many(
        {"ticker": ticker},
        {"$unset": {
            "TEA": "", "TEM": "", "duration": "",
            "convexity": "", "paridad": "",
        }},
    )
    print(f"✓ Reseteados: {res.modified_count} docs")
    print("  El motor_curvas los va a re-enriquecer en el próximo ciclo (5s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
