"""Cliente MAE MarketData.

Auth simple: header `x-api-key: <API_KEY>` en cada request.

Dos ambientes: prod y UAT. La variable `MAE_ENV` en `.env` decide cuál
base URL se usa:
    - prod → https://api.mae.com.ar
    - uat  → https://apiuat.mae.com.ar

Rate limit interno hard-cap de 30 req/min para no pegarle en ráfaga —
MAE no publica límites oficiales, mejor ser conservador.

Endpoints wrappeados (inicial):
    get_repo(page=1)   → /api/v1/mercado/cotizaciones/repo (paginado)

Diseñado genérico para sumar otros endpoints MAE más adelante
(/api/v1/mercado/cotizaciones/cauciones, /titulos, /acciones) sin cambios
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
    "prod": "https://api.mae.com.ar",
    "uat":  "https://apiuat.mae.com.ar",
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


def _get(
    path: str,
    params: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    """GET contra MAE con x-api-key + rate limit + error handling tipado.

    `extra_headers` permite sobrescribir/agregar headers desde el caller
    (útil para debuggear bloqueos del WAF probando variantes del auth header).
    """
    _ensure_configured()
    _wait_for_rate_limit()

    url = f"{_base_url()}{path}"
    # User-Agent + headers tipo navegador para evitar bloqueo del WAF (Incapsula).
    headers = {
        "x-api-key": MAE_API_KEY,
        "Accept": "application/json",
        "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Connection": "keep-alive",
    }
    if extra_headers:
        headers.update(extra_headers)
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


def get_repo(page: int = 1) -> Any:
    """Operaciones de Repo del MAE (paginado).

    GET /api/v1/mercado/cotizaciones/repo?pageNumber=<page>

    Response: lista de entidades Repo con fields:
        fecha (ISO datetime), rueda, moneda ("$"/"USD"), plazo (str, ej "003"),
        tasaApertura, ultimaTasa, tasaPP, tasaMinimo, tasaMaximo, cierreAyer,
        cantidad (VN), volumen (total), cantOperaciones, variacion.

    Si `pageNumber` no se especifica, default 1. Para recorrer todo hay que
    iterar hasta que devuelva lista vacía.
    """
    return _get("/api/v1/mercado/cotizaciones/repo", {"pageNumber": page})


def iter_repo_pages(max_pages: int = 50):
    """Itera páginas de /mercado/cotizaciones/repo hasta que venga vacía.

    Yields cada página (lista de dicts). `max_pages` como guardia por si la
    API no devuelve terminación.
    """
    for p in range(1, max_pages + 1):
        data = get_repo(page=p)
        if not data:
            return
        if not isinstance(data, list):
            logger.warning("repo page %d: shape inesperada %s", p, type(data).__name__)
            yield data
            return
        yield data
        if len(data) == 0:
            return
