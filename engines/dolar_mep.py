import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime
from zoneinfo import ZoneInfo

import pyRofex

from core.job_runs import JobRunLogger
from core.pg_mirror import write_native
from core.rofex_session import inicializar_sesion

TICKER_AL30  = "MERV - XMEV - AL30 - CI"
TICKER_AL30D = "MERV - XMEV - AL30D - CI"
TICKER_AL30C = "MERV - XMEV - AL30C - CI"   # AL30 Cable: para CCL
ART = ZoneInfo("America/Argentina/Buenos_Aires")


def obtener_offer(ticker):
    data = pyRofex.get_market_data(ticker, entries=[pyRofex.MarketDataEntry.OFFERS])
    offers = data.get("marketData", {}).get("OF", [])
    if not offers:
        raise ValueError(f"Sin offers para {ticker}")
    return float(offers[0]["price"])


def obtener_bid(ticker):
    data = pyRofex.get_market_data(ticker, entries=[pyRofex.MarketDataEntry.BIDS])
    bids = data.get("marketData", {}).get("BI", [])
    if not bids:
        raise ValueError(f"Sin bids para {ticker}")
    return float(bids[0]["price"])


def run(jr=None):
    ahora = datetime.now(tz=ART)
    print(f"[{ahora.strftime('%H:%M:%S')}] Calculando Dólar MEP...")

    if not inicializar_sesion():
        print("❌ No se pudo inicializar sesión.")
        if jr:
            jr.error("no se pudo inicializar la sesión pyRofex")
        return

    try:
        al30_offer  = obtener_offer(TICKER_AL30)
        al30d_bid   = obtener_bid(TICKER_AL30D)

        if al30d_bid <= 0:
            raise ValueError("AL30D bid es 0, no se puede calcular el tipo de cambio")

        mep = round(al30_offer / al30d_bid, 4)

        # SQL-NATIVE (decomiso Mongo): histórico → valuaciones.dolar (PK timestamp).
        # timestamp AWARE (ART) → timestamptz sin ambigüedad.
        doc = {
            "timestamp":  ahora,
            "al30_offer": al30_offer,
            "al30d_bid":  al30d_bid,
            "mep":        mep,
        }

        # CCL best-effort: si AL30C no tiene bid, no rompemos el job, pero
        # lo loggeamos. La query histórica filtra docs sin ccl naturalmente.
        try:
            al30c_bid = obtener_bid(TICKER_AL30C)
            if al30c_bid > 0:
                ccl   = round(al30_offer / al30c_bid, 4)
                canje = round((ccl - mep) / mep * 100, 2)
                doc["al30c_bid"] = al30c_bid
                doc["ccl"]       = ccl
                doc["canje"]     = canje
        except Exception as e_ccl:
            print(f"⚠ AL30C no disponible este turno: {e_ccl}. Se persiste solo MEP.")

        write_native("dolar", ["timestamp"], [doc])
        if jr:
            jr.set_stat("mep", mep)
            jr.set_stat("ccl", doc.get("ccl"))

        if "ccl" in doc:
            print(
                f"MEP={mep} (AL30 offer={al30_offer} / AL30D bid={al30d_bid})  "
                f"CCL={doc['ccl']} (AL30C bid={doc['al30c_bid']})  canje={doc['canje']}%"
            )
        else:
            print(f"MEP guardado: {mep} (AL30 offer={al30_offer} / AL30D bid={al30d_bid})")

    except Exception as e:
        print(f"Error: {e}")
        if jr:
            jr.error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    with JobRunLogger("dolar_mep") as _jr:
        run(jr=_jr)
