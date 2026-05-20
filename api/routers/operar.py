"""Router /api/operar — soporte para la vista "Operar Dashboard".

Endpoints utilitarios que la vista del dashboard consume para mostrar
order book y datos live de instrumentos arbitrarios (no solo dólar MEP).
El envío real de órdenes sigue yendo por `/api/ordenes` (no se duplica).

Por ahora único endpoint:
  GET /api/operar/order-book?ticker=X  → top 5 niveles bid/ask + meta

Gate RBAC se aplica en api/main.py vía `_OPERAR`.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from api.services.order_book import get_order_book

router = APIRouter(prefix="/api/operar", tags=["operar"])


@router.get("/order-book")
def get_book(
    ticker: str = Query(..., description="Ticker corto (AL30) o full ROFEX"),
    plazo: str = Query("24hs", description="CI | 24hs | 48hs (ignorado si ticker es full)"),
):
    """Top 5 niveles de un ticker arbitrario.

    Busca directo en MarketSnapshot — no depende de Trading.Curvas. Cubre
    cualquier instrumento que el motor esté suscribiendo. Si no existe en
    MarketSnapshot, devuelve 404 indicando que el motor no lo cubre.
    """
    book = get_order_book(ticker, plazo=plazo)
    if book is None:
        raise HTTPException(
            404,
            f"Ticker {ticker!r} (plazo={plazo}) no está en MarketSnapshot. "
            "El motor_rofex no lo está suscribiendo (verificar config.TICKERS_EXTRA_PRECIOS "
            "o Trading.Curvas).",
        )
    return book
