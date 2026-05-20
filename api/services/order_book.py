"""Capa de servicio — Order Book (LOB) live.

Lee Trading.MarketSnapshot, que popula engines/valores.py
(MicrostructureEngine) cada 1s con depth=5 desde pyRofex WS. Sin histórico
— solo el último estado vivo. Cobertura: TODOS los tickers que el motor
suscribe (Curvas + TICKERS_EXTRA_PRECIOS + lo que vaya sumando).

`get_order_book` acepta ticker full ('MERV - XMEV - AL30 - 24hs') o
corto + plazo ('AL30' + '24hs'). NO depende de Trading.Curvas — eso es
solo la lista de bonos con curva calculada, no el universo operable.

Latencia: ~10-50ms (read Mongo con índice por ticker). El delay vs mercado
real está dominado por el sleep(1) del snapshot_loop del motor.
"""
from __future__ import annotations

import re

from api.cache import cached
from api.db import get_db_trading

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

_PLAZOS_VALIDOS = ("CI", "24hs", "48hs")


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


def get_order_book(instrumento: str, plazo: str = "24hs") -> dict | None:
    """LOB live (depth 5) de un ticker arbitrario.

    Acepta:
      - Full ROFEX ('MERV - XMEV - AL30 - 24hs') → match exacto.
      - Corto ('AL30') + plazo ('CI'|'24hs'|'48hs') → substring match
        sobre MarketSnapshot.ticker.

    Resuelve directo contra MarketSnapshot (no pasa por Curvas) — así
    cubre cualquier instrumento que el motor esté suscribiendo, no solo
    los de la curva. Devuelve None si el motor no lo escribió todavía.

    Si hay múltiples matches con el mismo corto (raro: distinto plazo),
    devuelve el más recientemente actualizado.
    """
    instr = (instrumento or "").strip()
    if not instr:
        return None
    coll = get_db_trading()["MarketSnapshot"]

    if " - " in instr:
        return coll.find_one({"ticker": instr}, _PROJ)

    plazo_eff = plazo if plazo in _PLAZOS_VALIDOS else "24hs"
    # Regex anclada por separadores ' - ' a ambos lados del corto y del
    # plazo, así "AL30" no matchea "AL30D".
    pattern = rf" - {re.escape(instr)} - {re.escape(plazo_eff)}$"
    docs = list(
        coll.find({"ticker": {"$regex": pattern, "$options": "i"}}, _PROJ)
        .sort("updated_at", -1)
        .limit(1)
    )
    return docs[0] if docs else None


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
