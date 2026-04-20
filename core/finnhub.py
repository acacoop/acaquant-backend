"""Cliente Finnhub con rate limiting interno.

Finnhub free tier: 60 req/min por IP + key. Acá limitamos a 40 req/min para
dejar margen y no ir a 429 ante ráfagas. El contador es thread-safe (lock
compartido) — si mañana corren varios jobs en paralelo siguen respetando el
límite.

Expone wrappers de alto nivel para los endpoints que usamos:
    - general_news, company_news
    - quote, stock_candle, forex_candle
    - economic_calendar
    - profile (company profile v2)

Las excepciones vienen tipadas (FinnhubError). El caller decide si reintenta
o loggea.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests

from config import FINNHUB_API_KEY

logger = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"
MAX_CALLS_PER_MIN = 40  # hard-budget bajo el 60 real de free tier
_rate_lock = threading.Lock()
_calls_ts: list[float] = []


class FinnhubError(RuntimeError):
    """Error del cliente (auth, red, rate limit, respuesta inválida)."""


def _wait_for_rate_limit() -> None:
    with _rate_lock:
        now = time.time()
        # descartar calls > 60s atrás
        _calls_ts[:] = [t for t in _calls_ts if now - t < 60]
        if len(_calls_ts) >= MAX_CALLS_PER_MIN:
            sleep_s = 60 - (now - _calls_ts[0]) + 0.2
            if sleep_s > 0:
                logger.debug("rate limit cerca; sleep %.2fs", sleep_s)
                time.sleep(sleep_s)
                now = time.time()
                _calls_ts[:] = [t for t in _calls_ts if now - t < 60]
        _calls_ts.append(now)


def _get(path: str, params: dict[str, Any] | None = None, timeout: int = 15) -> Any:
    if not FINNHUB_API_KEY:
        raise FinnhubError("FINNHUB_API_KEY no configurada en .env")
    _wait_for_rate_limit()

    url = f"{BASE_URL}{path}"
    all_params: dict[str, Any] = dict(params or {})
    all_params["token"] = FINNHUB_API_KEY

    try:
        resp = requests.get(url, params=all_params, timeout=timeout)
    except requests.RequestException as e:
        raise FinnhubError(f"red: {e}") from e

    if resp.status_code == 429:
        raise FinnhubError("rate limit 429 (aun con throttle interno — raro)")
    if resp.status_code == 401:
        raise FinnhubError("auth: FINNHUB_API_KEY inválida")
    if resp.status_code != 200:
        raise FinnhubError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        return resp.json()
    except ValueError as e:
        raise FinnhubError(f"respuesta no-JSON: {resp.text[:200]}") from e


# ── Wrappers por endpoint ───────────────────────────────────────────────────


def general_news(category: str = "general") -> list[dict]:
    """Top news por categoría: general | forex | crypto | merger."""
    out = _get("/news", {"category": category})
    return out or []


def company_news(symbol: str, desde: str, hasta: str) -> list[dict]:
    """Notas por ticker. `desde`/`hasta`: YYYY-MM-DD."""
    out = _get("/company-news", {"symbol": symbol, "from": desde, "to": hasta})
    return out or []


def quote(symbol: str) -> dict:
    """Último quote. Devuelve {c,o,h,l,pc,t}."""
    out = _get("/quote", {"symbol": symbol})
    return out or {}


def stock_candle(symbol: str, resolution: str, desde: int, hasta: int) -> dict:
    """Velas OHLCV. Resolution: 1,5,15,30,60,D,W,M. Timestamps en epoch seconds."""
    out = _get("/stock/candle", {"symbol": symbol, "resolution": resolution, "from": desde, "to": hasta})
    return out or {}


def forex_candle(symbol: str, resolution: str, desde: int, hasta: int) -> dict:
    """Velas FX. Ej symbol='OANDA:EUR_USD'."""
    out = _get("/forex/candle", {"symbol": symbol, "resolution": resolution, "from": desde, "to": hasta})
    return out or {}


def economic_calendar() -> dict:
    """Calendario económico global (próx ~60 días forward).

    Devuelve {'economicCalendar': [{time, country, event, impact, actual,
    prev, estimate, unit}, ...]}.
    """
    out = _get("/calendar/economic")
    return out or {}


def company_profile(symbol: str) -> dict:
    """Metadata company: name, logo, sector, industry, marketCapitalization, etc."""
    out = _get("/stock/profile2", {"symbol": symbol})
    return out or {}
