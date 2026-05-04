"""Crea Trading.OrderBookL2 como Time Series Collection (idempotente).

Time Series Collections en MongoDB (>=5.0) están optimizadas para data
con timestamp + meta: compresión nativa ~60-70%, indexación automática
sobre (meta, time), queries por rango más rápidas. Una vez creada con
estos parámetros, el código que lee/escribe la usa con la misma API
(find/insert_many) que cualquier colección normal.

Uso:
    python -m scripts.init_orderbook_l2_collection           # crea si no existe
    python -m scripts.init_orderbook_l2_collection --info    # solo muestra el estado
"""
from __future__ import annotations

import sys

from core.mongo import get_mongo_client

DB_NAME = "Trading"
COL_NAME = "OrderBookL2"

TIMESERIES_OPTIONS = {
    "timeField":   "ts",
    "metaField":   "ticker",
    "granularity": "seconds",   # AL30 CI puede tener > 1 update/seg en horario activo
}


def main() -> int:
    info_only = "--info" in sys.argv
    db = get_mongo_client()[DB_NAME]

    existing = db.list_collection_names(filter={"name": COL_NAME})
    if existing:
        coll_info = next(
            db.list_collections(filter={"name": COL_NAME}), None,
        )
        opts = (coll_info or {}).get("options", {})
        ts_opts = opts.get("timeseries")
        if ts_opts:
            print(f"OK: {DB_NAME}.{COL_NAME} ya existe como Time Series Collection.")
            print(f"  timeField   = {ts_opts.get('timeField')}")
            print(f"  metaField   = {ts_opts.get('metaField')}")
            print(f"  granularity = {ts_opts.get('granularity')}")
        else:
            print(f"⚠ {DB_NAME}.{COL_NAME} existe pero NO es Time Series Collection.")
            print("  Para convertir: borrar y recrear (data se pierde).")
        return 0

    if info_only:
        print(f"{DB_NAME}.{COL_NAME} no existe. Corré sin --info para crearla.")
        return 0

    db.create_collection(COL_NAME, timeseries=TIMESERIES_OPTIONS)
    print(f"OK: {DB_NAME}.{COL_NAME} creada como Time Series Collection.")
    print(f"  timeField   = {TIMESERIES_OPTIONS['timeField']}")
    print(f"  metaField   = {TIMESERIES_OPTIONS['metaField']}")
    print(f"  granularity = {TIMESERIES_OPTIONS['granularity']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
