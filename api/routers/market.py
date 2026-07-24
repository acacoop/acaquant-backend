"""Router Market: watchlist quotes, economic calendar, candles históricos.

`/quotes` y `/calendar/economic` leen SQL (`api/services/market_sql`, tablas
`market.*`) — decomiso Mongo: Market.{Quotes,EconomicCalendar} dropeadas.
`/candle` y `/profile` pegan a APIs externas (Yahoo/Finnhub).
"""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from api.services import market_sql

router = APIRouter(prefix="/api/market", tags=["Market"])


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        return datetime.fromisoformat(s).replace(tzinfo=UTC)
    except ValueError:
        return None


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


@router.get("/calendar/economic")
def calendar_economic(
    desde:       str | None = Query(None, description="ISO date; default = ahora"),
    hasta:       str | None = Query(None, description="ISO date; default = +30 días"),
    importancia: int = Query(0, ge=0, le=3, description="0=todas, 1-3 = mínimo de impact"),
    country:     str | None = Query(None, description="Ej: US, AR, BR, EU"),
    limit:       int = Query(500, ge=1, le=1000),
):
    now = datetime.now(UTC)
    d_desde = _parse(desde) or now
    d_hasta = _parse(hasta) or (now + timedelta(days=30))
    return market_sql.calendar_economic(
        desde=d_desde, hasta=d_hasta, importancia=importancia,
        country=country, limit=limit)


@router.get("/candle")
def candle(
    symbol:     str = Query(..., description="Ticker (AAPL, GGAL, etc)"),
    resolution: str = Query("D", description="1,5,15,30,60,D,W,M"),
    desde:      str | None = Query(None),
    hasta:      str | None = Query(None),
):
    """Histórico OHLC vía Yahoo Finance (yfinance).

    Finnhub free bloqueó candles (403), así que usamos Yahoo. Cubre stocks,
    ETFs, ADRs e índices globalmente. Para FX usar endpoint separado o
    frankfurter.app directo.
    """
    from core.yahoo import YahooError, stock_candle

    now = datetime.now(UTC)
    d_desde = _parse(desde) or (now - timedelta(days=365))
    d_hasta = _parse(hasta) or now
    try:
        data = stock_candle(
            symbol.upper(),
            resolution,
            int(d_desde.timestamp()),
            int(d_hasta.timestamp()),
        )
    except YahooError as e:
        raise HTTPException(status_code=502, detail=f"Yahoo: {e}") from e

    status = data.get("s")
    if status != "ok":
        return {"symbol": symbol.upper(), "resolution": resolution,
                "candles": [], "status": status}

    times   = data.get("t") or []
    opens   = data.get("o") or []
    highs   = data.get("h") or []
    lows    = data.get("l") or []
    closes  = data.get("c") or []
    volumes = data.get("v") or []
    candles = [
        {
            "t": datetime.fromtimestamp(times[i], tz=UTC).isoformat(),
            "o": opens[i], "h": highs[i], "l": lows[i],
            "c": closes[i], "v": volumes[i],
        }
        for i in range(len(times))
    ]
    return {"symbol": symbol.upper(), "resolution": resolution, "candles": candles}


@router.get("/profile")
def profile(symbol: str = Query(...)):
    """Company profile v2 (nombre, sector, industry, market cap, logo)."""
    from core.finnhub import FinnhubError, company_profile
    try:
        data = company_profile(symbol.upper())
    except FinnhubError as e:
        raise HTTPException(status_code=502, detail=f"Finnhub: {e}") from e
    return data
