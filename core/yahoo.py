"""Cliente Yahoo Finance vía yfinance (gratis, sin API key).

Finnhub free tier bloqueó /stock/candle (403 en 2025). Para histórico usamos
Yahoo vía la librería `yfinance`, que es estable, gratuita y cubre todo lo
que necesitamos (US stocks, ETFs, ADRs, índices globales).

La función `stock_candle` emula el shape de Finnhub para que los callers
migren sin cambios: {s, t, o, h, l, c, v}.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_RES_MAP = {
    "1":  "1m",
    "5":  "5m",
    "15": "15m",
    "30": "30m",
    "60": "60m",
    "D":  "1d",
    "W":  "1wk",
    "M":  "1mo",
}


class YahooError(RuntimeError):
    pass


def stock_candle(symbol: str, resolution: str, desde_ts: int, hasta_ts: int) -> dict[str, Any]:
    """Histórico OHLCV desde Yahoo. Shape compatible con Finnhub stock_candle.

    Returns:
        {"s": "ok"|"no_data"|"error", "t": [...], "o": [...], "h": [...],
         "l": [...], "c": [...], "v": [...]}
    """
    try:
        import yfinance as yf
    except ImportError as e:
        raise YahooError("yfinance no instalado — pip install yfinance") from e

    interval = _RES_MAP.get(resolution, "1d")
    start = datetime.fromtimestamp(desde_ts, tz=timezone.utc)
    end   = datetime.fromtimestamp(hasta_ts, tz=timezone.utc)

    try:
        t = yf.Ticker(symbol)
        df = t.history(start=start, end=end, interval=interval, auto_adjust=False)
    except Exception as e:
        logger.warning("yfinance %s failed: %s", symbol, e)
        return {"s": "error", "error": str(e)}

    if df is None or df.empty:
        return {"s": "no_data"}

    try:
        times = [int(idx.timestamp()) for idx in df.index]
        return {
            "s": "ok",
            "t": times,
            "o": [float(v) for v in df["Open"].tolist()],
            "h": [float(v) for v in df["High"].tolist()],
            "l": [float(v) for v in df["Low"].tolist()],
            "c": [float(v) for v in df["Close"].tolist()],
            "v": [float(v) for v in df["Volume"].tolist()],
        }
    except Exception as e:
        logger.warning("yfinance parse %s failed: %s", symbol, e)
        return {"s": "error", "error": str(e)}
