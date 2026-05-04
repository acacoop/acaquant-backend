"""Capa de servicio — Order Book L2 histórico.

Lee Trading.OrderBookL2 (Time Series Collection que popula
engines/order_book_l2.py). A diferencia de api/services/order_book.py
(que devuelve el último estado vivo de Trading.MarketSnapshot), acá
servimos series temporales: cada cambio del book persistido entre dos
timestamps.

Sin cache — queries históricas son únicas por rango. La TS Collection
ya está optimizada en compresión + indexación sobre (ticker, ts).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.db import get_db_trading

COL_NAME = "OrderBookL2"

# Defensa contra payloads gigantes. AL30 CI puede tener ~50k docs/día;
# 10k a 500B = ~5MB de payload, manejable. Más que eso es ruido para
# análisis humano y mejor pedir ventanas más chicas.
MAX_LIMIT = 10000
DEFAULT_LIMIT = 1000


def _parse_ts(s: str | None) -> datetime | None:
    """ISO datetime → datetime UTC. None si vacío o inválido."""
    if not s:
        return None
    try:
        s = s.strip()
        if "T" not in s:
            # Solo fecha: asume 00:00:00 UTC
            return datetime.fromisoformat(s).replace(tzinfo=UTC)
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def get_orderbook_historico(
    ticker: str,
    desde: str | None = None,
    hasta: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """Serie de estados del book entre dos timestamps.

    Args:
        ticker: ticker completo (`MERV - XMEV - AL30 - CI`). El corto NO
                resuelve acá porque los tickers de OrderBookL2 pueden no
                estar en Trading.Curvas.
        desde: ISO datetime (`2026-05-04T13:00:00Z`) o fecha (`2026-05-04`).
                Default: 1 hora atrás desde `hasta` (o desde ahora si no hay).
        hasta: ISO datetime o fecha. Default: ahora UTC.
        limit: máximo de docs a devolver (default 1000, max 10000).
                Ordenado ascendente por ts; si la ventana excede limit,
                devuelve los PRIMEROS `limit` docs (no los últimos).

    Returns:
        Lista de docs: `[{ts, ticker, bids, offers}, ...]` ordenada
        ascendente por ts. Vacía si no hay data en el rango o el ticker
        es inválido.
    """
    if not ticker or " - " not in ticker:
        return []

    lim = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))

    d_hasta = _parse_ts(hasta) or datetime.now(UTC)
    d_desde = _parse_ts(desde) or (d_hasta - timedelta(hours=1))

    if d_desde >= d_hasta:
        return []

    db = get_db_trading()
    cursor = (
        db[COL_NAME]
        .find(
            {
                "ticker": ticker,
                "ts": {"$gte": d_desde, "$lte": d_hasta},
            },
            {"_id": 0, "ts": 1, "ticker": 1, "bids": 1, "offers": 1},
        )
        .sort("ts", 1)
        .limit(lim)
    )
    return list(cursor)


def listar_tickers_disponibles() -> list[str]:
    """Distinct tickers en OrderBookL2. Útil para validar qué activos
    tienen captura habilitada actualmente. Cacheable a futuro si crece."""
    db = get_db_trading()
    return sorted(db[COL_NAME].distinct("ticker"))
