"""GET /api/calendario — calendario económico (watchlist HOME, tab CALENDARIO).

Lee home.market_calendar (AR/US/BR de alto impacto, feed FMP). Público como el
resto de la watchlist de mercado.
"""
from __future__ import annotations

from fastapi import APIRouter

from api.services.calendario import get_calendario

router = APIRouter(prefix="/api/calendario", tags=["Calendario"])


@router.get("")
def calendario(dias: int = 45) -> list[dict]:
    """Próximos eventos del calendario (hasta `dias` adelante)."""
    return get_calendario(dias=dias)
