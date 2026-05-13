"""setup_precios_acciones.py — crea Trading.PreciosAcciones como
colección Time Series (Mongo 5.0+).

Shape de cada doc:
  {
    fecha:  ISODate("2026-05-12T00:00:00Z"),  // UTC midnight del día hábil
    ticker: "NVDA",                            // underlying US, NOT BYMA
    open:   218.55,
    high:   223.75,
    low:    214.92,
    close:  220.78,
    volume: 158666400,
  }

Config Time Series:
  - timeField:  "fecha"
  - metaField:  "ticker"
  - granularity: "hours"  (datos diarios → hours es eficiente, agrupa buckets
                            de ~1h × ticker — overhead mínimo)

Ventaja del TS sobre colección normal:
  - Compresión columnar nativa (~70% menos espacio que docs separados).
  - Queries por rango de tiempo + ticker son O(log) por índice automático.
  - 27 tickers × 365 días = ~9,855 docs → ~50KB después de compresión.

Idempotente: si la colección ya existe (cualquier tipo), no la toca y avisa.
Para recrear de cero hay que dropearla a mano primero.

Uso:
    python -m scripts.setup_precios_acciones
"""
from __future__ import annotations

from core.mongo import get_mongo_client


def run() -> None:
    print("=" * 80)
    print("SETUP Trading.PreciosAcciones (Time Series)")
    print("=" * 80)

    client = get_mongo_client()
    db = client["Trading"]

    existentes = set(db.list_collection_names())
    if "PreciosAcciones" in existentes:
        info = db.command("collStats", "PreciosAcciones")
        print(f"  ⚠ Ya existe Trading.PreciosAcciones — count={info.get('count', 0):,}")
        print(f"    Para recrearla, dropearla a mano antes:")
        print(f"      db.getSiblingDB('Trading').PreciosAcciones.drop()")
        return

    db.create_collection(
        "PreciosAcciones",
        timeseries={
            "timeField":   "fecha",
            "metaField":   "ticker",
            "granularity": "hours",
        },
    )
    print("  ✓ Trading.PreciosAcciones creada como Time Series")
    print(f"      timeField=fecha · metaField=ticker · granularity=hours")
    print("\n  Próximo paso: python -m scripts.backfill_precios_acciones")


if __name__ == "__main__":
    run()
