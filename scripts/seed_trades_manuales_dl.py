"""seed_trades_manuales_dl.py — 4 trades sintéticos para dolar-linked.

Inyecta un trade único por bono dolar-linked para que aparezcan en la
tabla de Renta Fija → DOLAR LINKED con precio, TEA en USD y paridad.
Útil para validar la UI sin esperar trade real en mercado.

Precios cargados (ARS por cada 100 de VN USD):
    TZV26 → 140.150
    D30S6 → 141.500
    TZV27 → 134.500
    TZV28 → 120.104

Shape conforme con engines/valores.py (motor real):
    TimeSales: {ticker, timestamp, price, size, side, money}
               donde money = (price / 100) × size (convención AR).
    MarketSnapshot.metrics: SOLO last_price + vwap + total_nominals.
               Open/high/low/close no se inventan — quedan ausentes
               para que el frontend los muestre como '--'.

ANTES de insertar, borra cualquier trade sintético previo de los
mismos tickers (filtrado por size=1 + side='' que es el shape de los
sintéticos) para no acumular basura en TimeSales.

Uso (post seed_dolar_linked + restart motor_curvas):
    python -m scripts.seed_trades_manuales_dl
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

        # 1) Borrar cualquier trade sintético previo del ticker — son los
        #    que dejamos con size=1 y side="" (un trade real siempre
        #    tiene side BUY/SELL y size > 1 normalmente).
        del_trades = col_ts.delete_many({
            "ticker": ticker_completo,
            "size": 1.0,
            "side": "",
        })
        if del_trades.deleted_count:
            logger.info("  - %s: borrados %d trades sintéticos previos",
                        corto, del_trades.deleted_count)

        # 2) Insertar trade nuevo con shape igual al de motor_rofex.
        size = 1.0
        money = (precio / 100.0) * size   # convención AR: cash = precio% × size
        trade_doc = {
            "ticker":    ticker_completo,
            "timestamp": now,
            "price":     precio,
            "size":      size,
            "side":      "",
            "money":     money,
            # NO seteamos `duration` → motor_curvas lo enriquece en su tick.
        }
        col_ts.insert_one(trade_doc)

        # 3) Snapshot con shape del motor real — solo last_price + vwap +
        #    total_nominals son reales. Open/high/low/close se omiten para
        #    que el frontend muestre '--' (no inventamos valores).
        snap_set = {
            "ticker":     ticker_completo,
            "updated_at": now,
            "metrics": {
                "last_price":     precio,
                "vwap":           precio,
                "total_nominals": size,
            },
            "recent_trades": [{
                "timestamp": now, "price": precio, "size": size,
                "side": "", "money": money,
            }],
            "top_trades": [{
                "timestamp": now, "price": precio, "size": size,
                "side": "", "money": money,
            }],
            "book": {"bids": [], "offers": []},
        }
        # IMPORTANTE: $set sin upsert eliminaría campos que motor_rofex
        # haya escrito antes. Acá usamos upsert=True con $set parcial:
        # solo sobrescribe los campos del dict (preserva otros).
        col_ms.update_one(
            {"ticker": ticker_completo},
            {"$set": snap_set},
            upsert=True,
        )
        logger.info("✓ %s @ %.2f ARS  (snapshot OK, motor_curvas enriquece en ~5s)",
                    corto, precio)
        n_ok += 1

    logger.info("Resumen: ok=%d fail=%d (total=%d)", n_ok, n_fail, len(TRADES))


if __name__ == "__main__":
    main()
