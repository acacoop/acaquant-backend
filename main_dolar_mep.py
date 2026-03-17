import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime
from zoneinfo import ZoneInfo
from mongo_manager import get_mongo_client

TICKER_AL30  = "MERV - XMEV - AL30 - CI"
TICKER_AL30D = "MERV - XMEV - AL30D - CI"
ART = ZoneInfo("America/Argentina/Buenos_Aires")


def obtener_precio(snapshot_col, ticker, lado):
    """Devuelve el primer precio del lado 'bids' u 'offers' para un ticker."""
    doc = snapshot_col.find_one({"ticker": ticker})
    if doc is None:
        raise ValueError(f"No hay snapshot para {ticker}")
    precios = doc.get("book", {}).get(lado, [])
    if not precios:
        raise ValueError(f"Sin datos en book.{lado} para {ticker}")
    return float(precios[0]["price"])


def run():
    ahora = datetime.now(tz=ART)
    print(f"[{ahora.strftime('%H:%M:%S')}] Calculando Dólar MEP...")

    client = get_mongo_client()
    try:
        snapshot_col = client["Trading"]["MarketSnapshot"]
        dolar_col    = client["Valuaciones"]["Dolar"]

        al30_offer  = obtener_precio(snapshot_col, TICKER_AL30,  "offers")
        al30d_bid   = obtener_precio(snapshot_col, TICKER_AL30D, "bids")

        if al30d_bid <= 0:
            raise ValueError("AL30D bid es 0, no se puede calcular el tipo de cambio")

        mep = al30_offer / al30d_bid

        doc = {
            "timestamp":  ahora.replace(tzinfo=None),
            "al30_offer": al30_offer,
            "al30d_bid":  al30d_bid,
            "mep":        round(mep, 4),
        }

        dolar_col.insert_one(doc)
        print(f"✅ MEP: {doc['mep']} (AL30 offer={al30_offer} / AL30D bid={al30d_bid})")

    except Exception as e:
        print(f"🔥 Error: {e}")
    finally:
        client.close()


if __name__ == "__main__":
    run()
