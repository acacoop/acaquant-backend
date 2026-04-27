"""insert_trade_manual.py — inyectar un trade simulado para testear UI.

Caso de uso: cargaste un bono nuevo en Trading.Curvas (ej. dolar-linked
TZV26) pero todavía no operó en mercado, así que no aparece en
MarketSnapshot ni en el frontend. Este script simula un trade único
para que el bono entre a la cadena:

    1. inserta doc en Trading.TimeSales con price/size/side,
    2. crea/actualiza doc mínimo en Trading.MarketSnapshot,
    3. dispara automáticamente al motor_curvas (próximo tick, ≤ 5s)
       a calcular TEA / duration / paridad / mod_duration / convexity
       y propagar al MarketSnapshot.

Después de correrlo, en ≤ 10s el bono aparece en la tabla de Renta
Fija con todos los analíticos. NO opera nada en el mercado real —
es solo data sintética para validación de UI.

Uso:
    python -m scripts.insert_trade_manual TZV26 --price 95.5
    python -m scripts.insert_trade_manual TZV26 --price 95.5 --size 100 --side BUY
    python -m scripts.insert_trade_manual TZV26 --price 95.5 --vwap 95.4

Para limpiar después de testear:
    Borrá el trade en TimeSales y el doc en MarketSnapshot manualmente
    desde Atlas (filtrar por timestamp del último insert), o esperá a
    que aparezca un trade real que pise los datos.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime

from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("insert_trade_manual")


def resolver_ticker_completo(client, instrumento: str) -> tuple[str, dict] | None:
    """Devuelve (ticker_completo, doc_de_curva) o None si no se encontró."""
    if " - " in instrumento:
        ticker = instrumento
    else:
        # ticker_corto → buscamos el doc en Trading.Curvas
        ticker = None
    col = client["Trading"]["Curvas"]
    if ticker:
        doc = col.find_one({"ticker": ticker}, {"_id": 0})
    else:
        doc = col.find_one({"ticker_corto": instrumento}, {"_id": 0})
    if not doc:
        return None
    return doc["ticker"], doc


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("instrumento", help="Ticker corto (TZV26) o completo")
    parser.add_argument("--price", type=float, required=True, help="Precio del trade")
    parser.add_argument("--size", type=float, default=1.0, help="Tamaño (default 1)")
    parser.add_argument("--side", default="", help="BUY | SELL | '' (default vacío)")
    parser.add_argument(
        "--vwap", type=float, default=None,
        help="VWAP del día (default = price). Solo para el snapshot.",
    )
    parser.add_argument(
        "--total-nominals", type=float, default=None,
        help="Volumen total nominal del día (default = size).",
    )
    args = parser.parse_args()

    client = get_mongo_client()
    res = resolver_ticker_completo(client, args.instrumento)
    if not res:
        print(f"❌ No encontré {args.instrumento!r} en Trading.Curvas.", file=sys.stderr)
        sys.exit(1)

    ticker_completo, curva_doc = res
    ticker_corto = curva_doc.get("ticker_corto", "")
    curva = curva_doc.get("curva", "")
    now = datetime.now(UTC)
    money = args.price * args.size

    # 1) Insertar trade en TimeSales
    trade_doc = {
        "ticker":    ticker_completo,
        "timestamp": now,
        "price":     args.price,
        "size":      args.size,
        "side":      args.side,
        "money":     money,
        # NO seteamos `duration` para que motor_curvas lo agarre como
        # trade nuevo en su próximo tick y lo enriquezca.
    }
    client["Trading"]["TimeSales"].insert_one(trade_doc)
    logger.info(
        "TimeSales: trade insertado %s @ %.4f size=%s",
        ticker_corto, args.price, args.size,
    )

    # 2) Upsert mínimo en MarketSnapshot — solo lo necesario para que
    #    el frontend lo encuentre. motor_curvas le va a sumar las
    #    métricas analíticas en el próximo tick.
    vwap = args.vwap if args.vwap is not None else args.price
    total_nominals = args.total_nominals if args.total_nominals is not None else args.size
    snap_set = {
        "ticker":     ticker_completo,
        "updated_at": now,
        "metrics": {
            "last_price":     args.price,
            "vwap":           vwap,
            "open_price":     args.price,
            "high_price":     args.price,
            "low_price":      args.price,
            "closing_price":  args.price,
            "total_nominals": total_nominals,
        },
        "recent_trades": [trade_doc],
        "top_trades":    [trade_doc],
        "book": {"bids": [], "offers": []},
    }
    client["Trading"]["MarketSnapshot"].update_one(
        {"ticker": ticker_completo},
        {"$set": snap_set},
        upsert=True,
    )
    logger.info(
        "MarketSnapshot: upsert OK ticker=%s curva=%s precio=%.4f",
        ticker_corto, curva, args.price,
    )

    logger.info(
        "Listo. motor_curvas debería enriquecer en ≤ 5s. "
        "Verificá con: python -m scripts.inspect_market_snapshot %s",
        ticker_corto,
    )


if __name__ == "__main__":
    main()
