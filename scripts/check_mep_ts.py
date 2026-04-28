"""Diagnóstico de cotización MEP — chequea Trading.TimeSales para los 4 tickers
de la operativa Dolar MEP (AL30/AL30D × CI/24hs).

Uso:
    python -m scripts.check_mep_ts
"""
from __future__ import annotations

from datetime import UTC, datetime

from dotenv import load_dotenv

load_dotenv()

from core.mongo import get_mongo_client_read  # noqa: E402

TICKERS = [
    "MERV - XMEV - AL30 - CI",
    "MERV - XMEV - AL30D - CI",
    "MERV - XMEV - AL30 - 24hs",
    "MERV - XMEV - AL30D - 24hs",
]


def main() -> None:
    db = get_mongo_client_read()["Trading"]
    ahora = datetime.now(UTC)
    print(f"\nDiagnóstico Trading.TimeSales — ahora UTC = {ahora.isoformat()}\n")
    print(f"{'TICKER':40}  {'PRICE':>10}  {'TIMESTAMP':30}  {'EDAD':>10}")
    print("-" * 100)

    for t in TICKERS:
        last = db["TimeSales"].find_one({"ticker": t}, sort=[("timestamp", -1)])
        if not last:
            print(f"{t:40}  {'—':>10}  {'SIN TRADES':30}  {'':>10}")
            continue
        ts = last.get("timestamp")
        if isinstance(ts, datetime):
            ts_aware = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
            edad_s = (ahora - ts_aware).total_seconds()
            edad_str = f"{edad_s:>8.0f}s" if edad_s < 3600 else f"{edad_s / 60:>7.0f}min"
            ts_str = ts_aware.isoformat()
        else:
            edad_str = "?"
            ts_str = str(ts)
        price = last.get("price")
        print(f"{t:40}  {price:>10}  {ts_str:30}  {edad_str:>10}")

    # Conteo total de trades hoy por ticker — para ver si se está suscribiendo
    inicio_hoy = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    print(f"\nTrades hoy (desde {inicio_hoy.isoformat()}):")
    for t in TICKERS:
        n = db["TimeSales"].count_documents(
            {"ticker": t, "timestamp": {"$gte": inicio_hoy}},
        )
        print(f"  {t:40}  {n:>6} trades")

    print()


if __name__ == "__main__":
    main()
