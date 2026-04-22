"""Borra los campos enriquecidos de trades en Trading.TimeSales.

Uso típico: tras cambiar el seed de un bono (ticker, flujos, curva) o
la lógica del motor_curvas. Los trades previos quedan con valores
viejos y el motor no los re-procesa porque solo busca docs SIN
`duration`. Este script unsetea los 5 campos (TEA, TEM, duration,
convexity, paridad) para que el próximo ciclo los recalcule.

Uso:
    python -m scripts.reset_enrich GD35D GD38D
    python -m scripts.reset_enrich "MERV - XMEV - TX26 - 24hs"
    python -m scripts.reset_enrich --all-soberanos
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client


def _resolver_tickers(client, ticker_or_short: str) -> list[str]:
    """Si no trae ' - ', lo trata como ticker_corto y resuelve via Curvas."""
    if " - " in ticker_or_short:
        return [ticker_or_short]
    doc = client["Trading"]["Curvas"].find_one(
        {"ticker_corto": ticker_or_short}, {"ticker": 1, "_id": 0},
    )
    if not doc or not doc.get("ticker"):
        raise SystemExit(
            f"✗ No existe ticker_corto='{ticker_or_short}' en Trading.Curvas"
        )
    return [doc["ticker"]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "tickers", nargs="*",
        help="Ticker corto o completo (ej. GD35D o 'MERV - XMEV - GD35D - 24hs').",
    )
    parser.add_argument(
        "--all-soberanos", action="store_true",
        help="Resetea todos los tickers con curva='soberanos' en Trading.Curvas",
    )
    args = parser.parse_args()

    client = get_mongo_client()

    tickers: list[str] = []
    if args.all_soberanos:
        docs = list(client["Trading"]["Curvas"].find(
            {"curva": "soberanos"}, {"ticker": 1, "_id": 0},
        ))
        tickers = [d["ticker"] for d in docs if d.get("ticker")]
        if not tickers:
            print("✗ No hay tickers con curva='soberanos' en Trading.Curvas")
            return 1
    elif args.tickers:
        for t in args.tickers:
            tickers.extend(_resolver_tickers(client, t))
    else:
        parser.error("Pasá al menos un ticker o --all-soberanos")

    col = client["Trading"]["TimeSales"]
    total = 0
    for t in tickers:
        antes_total = col.count_documents({"ticker": t})
        antes_enrich = col.count_documents({"ticker": t, "duration": {"$exists": True}})
        r = col.update_many(
            {"ticker": t},
            {"$unset": {
                "TEA": "", "TEM": "", "duration": "",
                "convexity": "", "paridad": "",
            }},
        )
        print(f"{t:<45} docs={antes_total:>5}  con_enrich={antes_enrich:>5}  reset={r.modified_count}")
        total += r.modified_count

    print(f"\nTotal reseteados: {total}")
    if total > 0:
        print("El motor_curvas los re-enriquece en el próximo ciclo (5s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
