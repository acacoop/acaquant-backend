"""Cliente FMP (financialmodelingprep) — calendario económico.

Reemplazo de Finnhub para el calendario (Finnhub free dejó de traer datos ~2026).
Free tier: 250 req/día — con 1 pull diario sobra. Auth por query param `apikey`.

FMP tiene DOS bases: `/api/v3` (legacy, en vías de baja) y `/stable` (la actual,
donde el calendario se llama `economic-calendar` con guión). Ver
docs/SECRETS.md y scripts/diag_calendario.py.

Excepciones tipadas (FmpError). El caller decide si reintenta / loggea.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from config import FMP_API_KEY

logger = logging.getLogger(__name__)

BASE_URL = "https://financialmodelingprep.com/api/v3"      # legacy
BASE_URL_STABLE = "https://financialmodelingprep.com/stable"


class FmpError(RuntimeError):
    """Error del cliente FMP (auth, red, rate limit, respuesta inválida)."""


def _get(path: str, params: dict[str, Any] | None = None, timeout: int = 20,
         base: str | None = None) -> Any:
    if not FMP_API_KEY:
        raise FmpError("FMP_API_KEY no configurada en .env")
    all_params: dict[str, Any] = dict(params or {})
    all_params["apikey"] = FMP_API_KEY
    try:
        resp = requests.get(f"{base or BASE_URL}{path}", params=all_params, timeout=timeout)
    except requests.RequestException as e:
        raise FmpError(f"red: {e}") from e

    # 401 y 403 son causas DISTINTAS y se arreglan distinto: 401 = la key no
    # sirve (rotarla); 403 = la key es válida pero el plan no cubre el endpoint
    # (pagar o cambiar de proveedor). El cuerpo de FMP explica cuál es.
    if resp.status_code == 401:
        raise FmpError(f"401 key inválida/ausente — {resp.text[:200]}")
    if resp.status_code == 403:
        raise FmpError(f"403 endpoint fuera del plan contratado — {resp.text[:200]}")
    if resp.status_code == 429:
        raise FmpError("rate limit 429 (250 req/día del free tier agotadas)")
    if resp.status_code != 200:
        raise FmpError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        return resp.json()
    except ValueError as e:
        raise FmpError(f"respuesta no-JSON: {resp.text[:200]}") from e


def economic_calendar(desde: str, hasta: str) -> list[dict]:
    """Calendario económico entre `desde` y `hasta` (YYYY-MM-DD). Rango máx 90 días.

    Devuelve una LISTA de eventos, cada uno:
      {date, country, event, currency, previous, estimate, actual, change,
       impact ('Low'|'Medium'|'High'|...), unit, changePercentage}.
    """
    out = _get("/economic-calendar", {"from": desde, "to": hasta}, base=BASE_URL_STABLE)
    if isinstance(out, dict) and out.get("Error Message"):
        raise FmpError(f"FMP: {out['Error Message']}")
    return out or []
