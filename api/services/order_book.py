"""Capa de servicio — Order Book (LOB) live de activos de curvas.

Lee Trading.MarketSnapshot, que popula engines/valores.py
(MicrostructureEngine) cada 1s con depth=5 desde pyRofex WS. Sin histórico
— solo el último estado vivo. Cobertura: tickers de Trading.Curvas.

Latencia: ~10-50ms (read Mongo con índice por ticker). El delay vs mercado
real está dominado por el sleep(1) del snapshot_loop del motor — esta capa
NO cachea para minimizar latencia adicional.
"""
from __future__ import annotations

from api.cache import cached
from api.db import get_db_trading
from api.services.renta_fija import resolver_ticker_exacto

_CURVAS_VALIDAS = ("cer", "tasa_fija", "tamar", "soberanos", "dolar_linked")

# Proyección acotada: solo bids/offers + meta básico. Sin top_trades ni
# recent_trades ni metrics analíticas (TEA/duration/etc) — no es lo que
# pide un LOB y agregaría payload.
_PROJ = {
    "_id":                       0,
    "ticker":                    1,
    "updated_at":                1,
    "book.bids":                 1,
    "book.offers":               1,
    "metrics.last_price":        1,
    "metrics.open_price":        1,
    "metrics.high_price":        1,
    "metrics.low_price":         1,
    "metrics.closing_price":     1,
}


@cached(ttl=300)
def _tickers_de_curva(curva: str) -> list[str]:
    """Lista de tickers ROFEX completos de una curva. Cacheable: Trading.Curvas
    cambia rara vez (alta de instrumento) — TTL 5min es seguro."""
    if curva not in _CURVAS_VALIDAS:
        return []
    db = get_db_trading()
    return [
        d["ticker"]
        for d in db["Curvas"].find({"curva": curva}, {"_id": 0, "ticker": 1})
        if d.get("ticker")
    ]


def get_order_book(instrumento: str) -> dict | None:
    """LOB live (depth 5) de un ticker.

    Acepta corto ('TX26') o completo ('MERV - XMEV - TX26 - 24hs'). Devuelve
    None si el ticker no está en Trading.Curvas o nunca recibió market data.
    Sin cache — fresh read en cada llamada.
    """
    ticker = resolver_ticker_exacto(instrumento)
    if not ticker:
        return None
    return get_db_trading()["MarketSnapshot"].find_one({"ticker": ticker}, _PROJ)


def get_order_books_curva(curva: str) -> list[dict]:
    """LOBs live (depth 5) de todos los tickers de la curva.

    curva ∈ {tasa_fija, cer, soberanos, tamar, dolar_linked}. Una sola
    query a MarketSnapshot con $in. Sin cache del book.
    """
    tickers = _tickers_de_curva(curva)
    if not tickers:
        return []
    return list(
        get_db_trading()["MarketSnapshot"].find({"ticker": {"$in": tickers}}, _PROJ)
    )
