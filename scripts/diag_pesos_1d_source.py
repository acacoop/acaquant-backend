"""Diag READ-ONLY: de dónde sale 'MERV - XMEV - PESOS - 1D' (caución) en el motor.

El motor_rofex suscribe Trading.Curvas + config.TICKERS_EXTRA_PRECIOS +
Trading.AdhocSubscriptions. Este diag busca tickers de caución (PESOS/DOLAR a
plazo Nd) en cada fuente para saber cuál hay que limpiar de raíz.

Uso:
    python -m scripts.diag_pesos_1d_source
"""
from __future__ import annotations

import re

import config
from core.mongo import get_mongo_client_read

_CAUCION = re.compile(r"(PESOS|DOLAR)\s*-\s*\d+D", re.I)


def main() -> int:
    cli = get_mongo_client_read()

    print("== Trading.Curvas ==")
    curvas = [d.get("ticker") for d in cli["Trading"]["Curvas"].find({}, {"ticker": 1, "_id": 0})]
    hits = sorted(t for t in curvas if t and _CAUCION.search(t))
    print(f"  {len(curvas)} tickers · {len(hits)} de caución: {hits}")

    print("== Trading.AdhocSubscriptions ==")
    adhoc = [d.get("ticker") for d in cli["Trading"]["AdhocSubscriptions"].find({}, {"ticker": 1, "_id": 0})]
    hits2 = sorted(t for t in adhoc if t and _CAUCION.search(t))
    print(f"  {len(adhoc)} adhoc total · {len(hits2)} de caución: {hits2}")

    print("== config.TICKERS_EXTRA_PRECIOS ==")
    extra = list(getattr(config, "TICKERS_EXTRA_PRECIOS", []) or [])
    hits3 = sorted(t for t in extra if _CAUCION.search(str(t)))
    print(f"  {len(extra)} extra · de caución: {hits3}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
