"""Router Market: watchlist quotes, economic calendar, candles históricos."""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from core.mongo import get_mongo_client_read

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


def _serialize(d: dict) -> dict:
    d = dict(d)
    d.pop("_id", None)
    for k in ("timestamp", "updated_at", "fetched_at", "anchors_updated_at"):
        v = d.get(k)
        if isinstance(v, datetime):
            d[k] = v.astimezone(UTC).isoformat()

    # Compute retornos on-the-fly desde anchors
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


@router.get("/quotes")
def quotes(symbols: str | None = Query(None, description="CSV de símbolos; vacío = todos")):
    """Últimas cotizaciones desde Market.Quotes (población por jobs.market_quotes)."""
    coll = get_mongo_client_read()["Market"]["Quotes"]
    filtro: dict = {}
    if symbols:
        syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        filtro["symbol"] = {"$in": syms}
    docs = [_serialize(d) for d in coll.find(filtro)]
    # Ordenar por grupo y luego symbol
    docs.sort(key=lambda d: (d.get("grupo", "ZZZ"), d.get("symbol", "")))
    return docs


@router.get("/calendar/economic")
def calendar_economic(
    desde:       str | None = Query(None, description="ISO date; default = ahora"),
    hasta:       str | None = Query(None, description="ISO date; default = +30 días"),
    importancia: int = Query(0, ge=0, le=3, description="0=todas, 1-3 = mínimo de impact"),
    country:     str | None = Query(None, description="Ej: US, AR, BR, EU"),
    limit:       int = Query(500, ge=1, le=1000),
):
    coll = get_mongo_client_read()["Market"]["EconomicCalendar"]
    now = datetime.now(UTC)
    d_desde = _parse(desde) or now
    d_hasta = _parse(hasta) or (now + timedelta(days=30))

    filtro: dict = {"time": {"$gte": d_desde, "$lte": d_hasta}}
    if importancia:
        filtro["impact"] = {"$gte": importancia}
    if country:
        filtro["country"] = country.upper()

    cur = coll.find(filtro, {"_id": 0}).sort("time", 1).limit(limit)
    return [_serialize(d) for d in cur]


@router.get("/candle")
def candle(
    symbol:     str = Query(..., description="Ticker (AAPL, GGAL, etc)"),
    resolution: str = Query("D", description="1,5,15,30,60,D,W,M"),
    desde:      str | None = Query(None),
    hasta:      str | None = Query(None),
):
    """Histórico OHLC directo a Finnhub.

    Free tier solo tiene US stocks + ETFs. Índices vía ETF proxy (SPY, QQQ,
    DIA). FX no acá (usar /forex/candle si hace falta; no expuesto al
    frontend por ahora).
    """
    from core.finnhub import FinnhubError, stock_candle

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
    except FinnhubError as e:
        raise HTTPException(status_code=502, detail=f"Finnhub: {e}") from e

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
