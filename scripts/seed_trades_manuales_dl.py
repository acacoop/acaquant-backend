"""seed_trades_manuales_dl.py — 4 trades sintéticos para dolar-linked.

Inyecta un trade único por bono dolar-linked para que aparezcan en la
tabla de Renta Fija → DOLAR LINKED con precio, TEA en USD y paridad
calculadas. Útil para validar la UI sin esperar trade real en mercado.

Precios cargados (ARS por cada 100 de VN USD):
    TZV26 → 140.150
    D30S6 → 141.500
    TZV27 → 134.500
    TZV28 → 120.104

Wrapper sobre scripts.insert_trade_manual: hace lo mismo (insert en
TimeSales + upsert en MarketSnapshot) pero para los 4 bonos en una
sola corrida.

Uso (post seed_dolar_linked + restart motor_curvas):
    python -m scripts.seed_trades_manuales_dl

Después de ~5-10s motor_curvas enriquece y los 4 bonos aparecen en el
frontend con TEA / duration / paridad / mod_duration.

Borrable post-validación. Para limpiar los snapshots sintéticos sin
que motor real los pise: borrar manualmente los docs en TimeSales y
MarketSnapshot por ticker.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("seed_trades_manuales_dl")


# (ticker_corto, precio_pesos) — orden por vto ascendente.
TRADES: list[tuple[str, float]] = [
    ("TZV26", 140150.0),
    ("D30S6", 141500.0),
    ("TZV27", 134500.0),
    ("TZV28", 120104.0),
]

# El precio que cotizan estos bonos es por cada 100 de VN. El frontend lo
# muestra tal cual. Si en algún momento vienen como precio por 1 USD (no
# por 100), ajustar acá.


def _resolver_ticker(client, corto: str) -> tuple[str, dict] | None:
    doc = client["Trading"]["Curvas"].find_one({"ticker_corto": corto}, {"_id": 0})
    if not doc:
        return None
    return doc["ticker"], doc


def main() -> None:
    client = get_mongo_client()
    col_ts = client["Trading"]["TimeSales"]
    col_ms = client["Trading"]["MarketSnapshot"]
    now = datetime.now(UTC)

    n_ok = 0
    n_fail = 0
    for corto, precio in TRADES:
        res = _resolver_ticker(client, corto)
        if not res:
            logger.error("❌ %s: no encontré ticker en Trading.Curvas", corto)
            n_fail += 1
            continue
        ticker_completo, _ = res

        trade_doc = {
            "ticker":    ticker_completo,
            "timestamp": now,
            "price":     precio,
            "size":      1.0,
            "side":      "",
            "money":     precio,
            # Sin `duration` → motor_curvas lo agarra como trade nuevo.
        }
        col_ts.insert_one(trade_doc)

        snap_set = {
            "ticker":     ticker_completo,
            "updated_at": now,
            "metrics": {
                "last_price":     precio,
                "vwap":           precio,
                "open_price":     precio,
                "high_price":     precio,
                "low_price":      precio,
                "closing_price":  precio,
                "total_nominals": 1.0,
            },
            "recent_trades": [trade_doc],
            "top_trades":    [trade_doc],
            "book": {"bids": [], "offers": []},
        }
        col_ms.update_one(
            {"ticker": ticker_completo},
            {"$set": snap_set},
            upsert=True,
        )
        logger.info("✓ %s @ %.0f ARS  (snapshot OK, motor_curvas enriquece en ~5s)", corto, precio)
        n_ok += 1

    logger.info("Resumen: ok=%d fail=%d (total=%d)", n_ok, n_fail, len(TRADES))


if __name__ == "__main__":
    main()
