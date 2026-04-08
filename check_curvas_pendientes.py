"""
check_curvas_pendientes.py — Muestra cuántos docs sin duration hay por ticker en TimeSales.

Útil para detectar si hay tickers bloqueando el batch del motor de curvas.

Uso:
    python check_curvas_pendientes.py
"""

from collections import Counter
from mongo_manager import get_mongo_client


def main():
    client = get_mongo_client()
    col = client["Trading"]["TimeSales"]

    print("Contando docs sin duration por ticker...")
    docs = list(col.find({"duration": {"$exists": False}}, {"ticker": 1}))

    if not docs:
        print("No hay docs pendientes de enriquecer.")
        return

    conteo = Counter(d["ticker"] for d in docs)
    total = sum(conteo.values())

    print(f"\nTotal pendientes: {total}\n")
    print(f"{'Pendientes':>10}  Ticker")
    print("-" * 60)
    for ticker, n in sorted(conteo.items(), key=lambda x: -x[1]):
        print(f"{n:>10}  {ticker}")


if __name__ == "__main__":
    main()
