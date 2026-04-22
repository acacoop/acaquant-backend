"""Cliente MAE MarketData.

Auth simple: header `x-api-key: <API_KEY>` en cada request.

Dos ambientes: prod y UAT. La variable `MAE_ENV` en `.env` decide cuál
base URL se usa:
    - prod → https://api.mae.com.ar/MarketData/v1
    - uat  → https://apiuat.mae.com.ar/MarketData/v1

Rate limit interno hard-cap de 30 req/min para no pegarle en ráfaga —
MAE no publica límites oficiales, mejor ser conservador.

Endpoints wrappeados (inicial):
    get_repo(desde, hasta)   → /mercado/repo (rango de fechas)

Diseñado genérico para sumar otros endpoints MAE más adelante
(/mercado/cauciones, /mercado/titulos, /mercado/acciones) sin cambios
en la infra de auth/rate-limit.

Excepciones:
    MaeNotConfigured    falta API_KEY en .env
    MaeAuthError        401/403
    MaeRateLimitError   429 o quota local
    MaeError            otros fallos (red, 5xx, payload inválido)
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests

from config import MAE_API_KEY, MAE_ENV

logger = logging.getLogger(__name__)

_BASE_URLS = {
    "prod": "https://api.mae.com.ar/MarketData/v1",
    "uat":  "https://apiuat.mae.com.ar/MarketData/v1",
}

# Rate limit interno. MAE no publica números pero 30/min es un techo prudente.
_MAX_CALLS_PER_MIN = 30
_rate_lock = threading.Lock()
_calls_ts: list[float] = []


# ─────────────────────────────────────────────────────────────────────────────
# Excepciones
# ─────────────────────────────────────────────────────────────────────────────


class MaeError(RuntimeError):
    """Error del cliente MAE."""


class MaeAuthError(MaeError):
    """401/403 — API key inválida o sin permisos."""


class MaeRateLimitError(MaeError):
    """429 o quota interna excedida."""


class MaeNotConfigured(MaeError):
    """Falta MAE_API_KEY en el entorno."""


# ─────────────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────────────


def _ensure_configured() -> None:
    if not MAE_API_KEY:
        raise MaeNotConfigured(
            "Falta MAE_API_KEY en .env. Conseguirla en marketdata.mae.com.ar."
        )


def _base_url() -> str:
    env = MAE_ENV or "prod"
    if env not in _BASE_URLS:
        logger.warning("MAE_ENV=%r desconocido, usando 'prod'.", env)
        env = "prod"
    return _BASE_URLS[env]


def _wait_for_rate_limit() -> None:
    with _rate_lock:
        now = time.time()
        while _calls_ts and now - _calls_ts[0] > 60:
            _calls_ts.pop(0)
        if len(_calls_ts) >= _MAX_CALLS_PER_MIN:
            wait = 60 - (now - _calls_ts[0]) + 0.1
            logger.warning("MAE rate limit interno: esperando %.1fs", wait)
            time.sleep(wait)
            now = time.time()
            while _calls_ts and now - _calls_ts[0] > 60:
                _calls_ts.pop(0)
        _calls_ts.append(now)


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    """GET contra MAE con x-api-key + rate limit + error handling tipado."""
    _ensure_configured()
    _wait_for_rate_limit()

    url = f"{_base_url()}{path}"
    headers = {
        "x-api-key": MAE_API_KEY,
        "Accept":    "application/json",
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=20)
    except requests.RequestException as e:
        raise MaeError(f"red falló en GET {path}: {e}") from e

    if r.status_code in (401, 403):
        raise MaeAuthError(f"{r.status_code} en {path}: {r.text[:400]}")
    if r.status_code == 429:
        raise MaeRateLimitError(f"429 en {path}: {r.text[:200]}")
    if r.status_code != 200:
        raise MaeError(f"{r.status_code} en {path}: {r.text[:400]}")

    try:
        return r.json()
    except ValueError as e:
        raise MaeError(f"respuesta no-JSON en {path}: {e}") from e


# ─────────────────────────────────────────────────────────────────────────────
# Métodos públicos
# ─────────────────────────────────────────────────────────────────────────────


def get_repo(desde: str | None = None, hasta: str | None = None) -> Any:
    """Operaciones de Repo del MAE en el rango dado.

    `desde` y `hasta` son strings de fecha (formato a confirmar con el
    response real — la doc dice "rango" pero no detalla el formato exacto;
    probablemente 'YYYY-MM-DD' o 'DD/MM/YYYY'). Sin params, pide todo lo
    que la API devuelva por default (probablemente último día o ventana
    reciente).

    La primera llamada del smoke test aclara el shape del response y los
    parámetros aceptados.
    """
    params: dict[str, Any] = {}
    if desde:
        params["desde"] = desde
    if hasta:
        params["hasta"] = hasta
    return _get("/mercado/repo", params or None)
