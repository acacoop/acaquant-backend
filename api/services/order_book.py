"""Capa de servicio — Order Book (LOB) live.

Lee mercado.market_snapshot (SQL), que populan engines/valores.py
(MicrostructureEngine, book+precios cada 1s) y curvas.py. Sin histórico — solo
el último estado vivo. Cobertura: TODOS los tickers que el motor suscribe.

`get_order_book` acepta ticker full ('MERV - XMEV - AL30 - 24hs') o
corto + plazo ('AL30' + '24hs'). El catálogo de tickers por curva sigue
saliendo de Trading.Curvas (Mongo, colección aparte no migrada aún).
"""
from __future__ import annotations

from api.cache import cached
from api.db import get_db_trading
from core.postgres import get_pool

_CURVAS_VALIDAS = ("cer", "tasa_fija", "tamar", "soberanos", "dolar_linked")
_PLAZOS_VALIDOS = ("CI", "24hs", "48hs")

# Métricas del LOB (sin TEA/duration: no es lo que pide un order book).
_LOB_METRICS = ["last_price", "open_price", "high_price", "low_price", "closing_price"]


def _shape(d: dict | None) -> dict | None:
    """Da forma {ticker, updated_at, book:{bids,offers}, metrics:{...}} desde una fila SQL."""
    if d is None:
        return None
    book = d.get("book") or {}
    ua = d.get("updated_at")
    return {
        "ticker":     d["ticker"],
        "updated_at": ua.isoformat() if hasattr(ua, "isoformat") else ua,
        "book":       {"bids": book.get("bids"), "offers": book.get("offers")},
        "metrics":    {k: (float(d[k]) if d.get(k) is not None else None) for k in _LOB_METRICS},
    }


def _lob_rows(where: str, params: tuple) -> list[dict]:
    cols = ", ".join(_LOB_METRICS)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT ticker, updated_at, book, {cols} FROM mercado.market_snapshot WHERE {where}",
            params,
        )
        names = [c.name for c in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]


@cached(ttl=300)
def _tickers_de_curva(curva: str) -> list[str]:
    """Tickers ROFEX completos de una curva, desde Trading.Curvas (Mongo, no migrada).
    Cacheable: Curvas cambia rara vez (alta de instrumento) — TTL 5min seguro."""
    if curva not in _CURVAS_VALIDAS:
        return []
    db = get_db_trading()
    return [
        d["ticker"]
        for d in db["Curvas"].find({"curva": curva}, {"_id": 0, "ticker": 1})
        if d.get("ticker")
    ]


def get_order_book(instrumento: str, plazo: str = "24hs") -> dict | None:
    """LOB live (depth 5) de un ticker. Full ROFEX → match exacto; corto + plazo →
    match por sufijo ' - <corto> - <plazo>'. SQL-only (mercado.market_snapshot).
    None si el motor no lo escribió todavía."""
    instr = (instrumento or "").strip()
    if not instr:
        return None

    if " - " in instr:
        rows = _lob_rows("ticker = %s", (instr,))
        return _shape(rows[0]) if rows else None

    plazo_eff = plazo if plazo in _PLAZOS_VALIDOS else "24hs"
    # Sufijo ' - AL30 - 24hs' anclado (ILIKE, escapando comodines LIKE) → el más fresco.
    suf = instr.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    plz = plazo_eff.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = _lob_rows(
        "ticker ILIKE %s ESCAPE '\\' ORDER BY updated_at DESC LIMIT 1",
        (f"% - {suf} - {plz}",),
    )
    return _shape(rows[0]) if rows else None


def get_order_books_curva(curva: str) -> list[dict]:
    """LOBs live (depth 5) de todos los tickers de la curva. SQL-only."""
    tickers = _tickers_de_curva(curva)
    if not tickers:
        return []
    return [_shape(d) for d in _lob_rows("ticker = ANY(%s)", (tickers,))]
