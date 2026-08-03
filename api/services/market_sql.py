"""api/services/market_sql.py — Market (watchlist HOME) leyendo Postgres.

Espejo del endpoint read-only `/quotes` de api/routers/market.py: el doc completo
viaja en `data jsonb` (fechas ya ISO) y los retornos se computan on-the-fly desde
los anchors (`compute_returns`).

`/candle` y `/profile` son APIs externas (Yahoo/Finnhub) — no tocan esta tabla.
"""
from __future__ import annotations

import re

from api.cache import cached
from api.services._sql import _q

# Claves que el _serialize del path Mongo emite con offset explícito (+00:00, vía
# astimezone(UTC)). En el jsonb quedaron naive (Mongo guarda naive-UTC) → se les
# agrega el offset para que el output sea byte-a-byte igual al path Mongo.
_TZ_KEYS = ("timestamp", "updated_at", "fetched_at", "anchors_updated_at")
_TZ_RE = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")


def _fix_tz(d: dict) -> dict:
    for k in _TZ_KEYS:
        v = d.get(k)
        if isinstance(v, str) and len(v) >= 19 and not _TZ_RE.search(v):
            d[k] = v + "+00:00"
    return d


def compute_returns(d: dict) -> dict:
    """Retornos on-the-fly desde anchors. ÚNICA implementación de la regla
    (el `_serialize` del router Mongo la invoca también)."""
    last = d.get("last")
    for key_ret, key_anchor in [
        ("ret_7d",  "anchor_7d"),
        ("ret_mtd", "anchor_mtd"),
        ("ret_ytd", "anchor_ytd"),
        ("ret_1y",  "anchor_1y"),
    ]:
        anchor = d.get(key_anchor)
        if last is not None and anchor:
            try:
                d[key_ret] = round((last - anchor) / anchor * 100, 2)
            except (TypeError, ZeroDivisionError):
                d[key_ret] = None
        else:
            d[key_ret] = None
    return d


@cached(ttl=3)
def _quotes_all() -> list[dict]:
    """Watchlist COMPLETO (todos los símbolos) — el path CALIENTE (pollea ~cada 2s).
    Cacheado 3s: el dato cambia ≤1×/min → 1 query a Postgres por ventana, no por request.
    Sin esto, cada poll agarra una conexión del pool → se agota → PoolTimeout
    (incidente 2026-06-16, tumbó la API)."""
    rows = _q("SELECT data FROM market_quotes")
    docs = [compute_returns(_fix_tz(dict(r["data"]))) for r in rows]
    docs.sort(key=lambda d: (d.get("grupo", "ZZZ"), d.get("symbol", "")))
    return docs


def quotes(symbols: list[str] | None = None) -> list[dict]:
    """Últimas cotizaciones del watchlist. `symbols` ya viene upper/strip del router."""
    if symbols:  # path raro (símbolos puntuales) → sin cache
        rows = _q("SELECT data FROM market_quotes WHERE symbol = ANY(%(s)s)", {"s": symbols})
        docs = [compute_returns(_fix_tz(dict(r["data"]))) for r in rows]
        docs.sort(key=lambda d: (d.get("grupo", "ZZZ"), d.get("symbol", "")))
        return docs
    return _quotes_all()
