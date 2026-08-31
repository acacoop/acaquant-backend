"""core/fred_api.py — cliente de la FRED API (Federal Reserve Bank of St. Louis).

Doc vivo: docs/RESEARCH.md. Alimenta la tab "Datos Internacionales" de la
vista Research (arranque: bloque Tasas USA). Feed ANCHO y SEPARADO de jobs/bcra.py
(motores) — no se toca nada existente.

Diferencia CLAVE vs core/bcra_api.py: FRED EXIGE API key (el BCRA no).
- Key gratis en fredaccount.stlouisfed.org/apikeys → env var `FRED_API_KEY`.
- Se manda como query param `api_key=` (NO header), obligatorio en todo endpoint.
- Sin la env, el cliente levanta ErrorFRED al primer request (feed deshabilitado).

Verificado (2026-07-19, ver docs/RESEARCH.md):
- Base: https://api.stlouisfed.org/fred — REST, todo GET con query params.
- Rate limit: 120 req/min por key → throttle 0.6s (holgado) + backoff en 429/5xx.
- file_type=json SIEMPRE (el default es XML — la trampa #1).
- Missing values vienen como el string "." → se descartan (no se castea a float).
- /series/observations pagina por count/offset (limit máx 100000).

Cortesía de uso: throttle CENTRALIZADO acá (el rate limit es por key → un solo
carril de salida). Cliente para jobs de fondo: la latencia no importa, nunca martilla.
"""
from __future__ import annotations

import logging
import os

from core.http_base import Throttle, get_json

logger = logging.getLogger(__name__)

_BASE = "https://api.stlouisfed.org/fred"
_MIN_INTERVALO_S = 0.6          # 120/min = 0.5s; 0.6 deja margen
_TIMEOUT = 30
_MAX_REINTENTOS = 4
_PAGE = 100000                  # límite máximo de observations por página

_throttle = Throttle(_MIN_INTERVALO_S)


class ErrorFRED(RuntimeError):
    """Fallo de la FRED API (HTTP no-200 tras reintentos, o falta la API key)."""


def _api_key() -> str:
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        raise ErrorFRED("FRED_API_KEY no está en el entorno (env var del Droplet)")
    return key


def _get(path: str, params: dict | None = None) -> dict:
    p = {"api_key": _api_key(), "file_type": "json", **(params or {})}
    # 400/403 no se reintentan (key inválida, series_id inexistente).
    return get_json(
        f"{_BASE}{path}", params=p, timeout=_TIMEOUT,
        exc=ErrorFRED, reintentos=_MAX_REINTENTOS, throttle=_throttle,
    )


def metadata(series_id: str) -> dict:
    """Metadata de UNA serie (/series): title, frequency, units,
    seasonal_adjustment_short, observation_start/end, last_updated, notes.
    OJO: FRED devuelve la lista bajo la clave `seriess` (sí, con doble s)."""
    d = _get("/series", {"series_id": series_id})
    filas = d.get("seriess") or []
    return filas[0] if filas else {}


def observaciones(series_id: str, desde: str | None = None,
                  hasta: str | None = None) -> list[dict]:
    """Puntos [{fecha, valor}] de una serie (/series/observations). Pide siempre
    units=lin (crudo) — las transformaciones (YoY / índice 100) se hacen on-the-fly
    en la vista. Descarta los missing ("." de FRED). Pagina si excede una página.

    desde/hasta son YYYY-MM-DD; se mandan como observation_start/end. Los defaults
    de FRED son absurdos (1776/9999) → SIEMPRE acotar desde el caller."""
    puntos: list[dict] = []
    offset = 0
    while True:
        params: dict = {"series_id": series_id, "limit": _PAGE, "offset": offset}
        if desde:
            params["observation_start"] = desde
        if hasta:
            params["observation_end"] = hasta
        d = _get("/series/observations", params)
        page = d.get("observations") or []
        puntos.extend(page)
        total = d.get("count")
        offset += _PAGE
        if not page or total is None or offset >= total:
            break
    out = []
    for p in puntos:
        f, v = p.get("date"), p.get("value")
        if not f or v is None or v == ".":       # "." = missing en FRED
            continue
        try:
            out.append({"fecha": f, "valor": float(v)})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda p: p["fecha"])
    return out
