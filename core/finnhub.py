"""Cliente Finnhub con rate limiting interno.

Finnhub free tier: 60 req/min por IP + key. Acá limitamos a 40 req/min para
dejar margen y no ir a 429 ante ráfagas. El contador es thread-safe
(core.http_base.RateLimiter) — si mañana corren varios jobs en paralelo siguen
respetando el límite.

Expone wrappers de alto nivel para los endpoints que usamos:
    - general_news, company_news
    - quote, stock_candle
    - profile (company profile v2)

Las excepciones vienen tipadas (FinnhubError). El caller decide si reintenta
o loggea.
"""
from __future__ import annotations

import logging
from typing import Any

from config import FINNHUB_API_KEY
from core.http_base import RateLimiter, get_json

logger = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"
MAX_CALLS_PER_MIN = 40  # hard-budget bajo el 60 real de free tier
_limiter = RateLimiter(MAX_CALLS_PER_MIN)


class FinnhubError(RuntimeError):
    """Error del cliente (auth, red, rate limit, respuesta inválida)."""


def _get(path: str, params: dict[str, Any] | None = None, timeout: int = 15) -> Any:
    if not FINNHUB_API_KEY:
        raise FinnhubError("FINNHUB_API_KEY no configurada en .env")
    all_params: dict[str, Any] = dict(params or {})
    all_params["token"] = FINNHUB_API_KEY
    return get_json(
        f"{BASE_URL}{path}", params=all_params, timeout=timeout,
        exc=FinnhubError, throttle=_limiter,
        mensajes_status={
            429: "rate limit 429 (aun con throttle interno — raro)",
            401: "auth: FINNHUB_API_KEY inválida",
        },
    )


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


def company_profile(symbol: str) -> dict:
    """Metadata company: name, logo, sector, industry, marketCapitalization, etc."""
    out = _get("/stock/profile2", {"symbol": symbol})
    return out or {}
