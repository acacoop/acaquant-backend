"""Router Market: watchlist quotes + titulares Eikon.

`/quotes` lee SQL (`api/services/market_sql`, tabla `home.market_quotes`).

`/candle` y `/profile`, que pegaban a Yahoo y Finnhub, se borraron el
2026-08-30: no los consumía nadie. `core/yahoo` y `core/finnhub` siguen
vivos — los usan 7 jobs.
"""

from fastapi import APIRouter, Query

from api.services import market_sql

router = APIRouter(prefix="/api/market", tags=["Market"])


@router.get("/quotes")
def quotes(symbols: str | None = Query(None, description="CSV de símbolos; vacío = todos")):
    """Últimas cotizaciones desde market.quotes (SQL; población por jobs.market_quotes)."""
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
    return market_sql.quotes(syms)


@router.get("/eikon-news")
def eikon_news(limit: int = Query(80, ge=1, le=200)):
    """Titulares Reuters del feed Eikon de oficina — tab NOTICIAS de la
    watchlist HOME. Solo se mueven con el feed prendido; queda lo último."""
    from core.eikon_news import listar_news
    return {"news": listar_news(limit=limit)}
