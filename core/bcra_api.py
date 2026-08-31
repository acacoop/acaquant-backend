"""core/bcra_api.py — cliente de la API de Estadísticas Monetarias del BCRA (v4).

Doc vivo: docs/RESEARCH.md. Alimenta la tab BCRA de la vista Research.
SOLO Monetarias v4 (cambiarias descartadas por el user 2026-07-18; v3 redundante).

API pública, sin key. Verificado contra la API viva (2026-07-18):
- Catálogo: GET /estadisticas/v4.0/monetarias → 1.581 variables, paginado
  metadata.resultset {count, offset, limit} (limit máx 1000 → 2 páginas).
- Serie:    GET /estadisticas/v4.0/monetarias/{id} → results:
  [{idVariable, detalle: [{fecha: "YYYY-MM-DD", valor: number}]}].
  `limit` funciona; `desde`/`hasta` son el patrón documentado (v3) → el caller
  SIEMPRE re-filtra por fecha client-side (defensa si la API los ignorara).

Cortesía de uso: throttle entre requests + backoff en 429/5xx. Patrón
mercado_1816: cliente para jobs de fondo, la latencia no importa. Nunca martilla.
"""
from __future__ import annotations

import logging

from core.http_base import Throttle, get_json

logger = logging.getLogger(__name__)

_BASE = "https://api.bcra.gob.ar/estadisticas/v4.0"
_MIN_INTERVALO_S = 1.5
_TIMEOUT = 45
_MAX_REINTENTOS = 4
_PAGE = 1000  # límite máximo por página (verificado)

_throttle = Throttle(_MIN_INTERVALO_S)


class ErrorBCRA(RuntimeError):
    """Fallo de la API del BCRA (HTTP no-200 tras reintentos)."""


def _get(path: str, params: dict | None = None) -> dict:
    return get_json(
        f"{_BASE}{path}", params=params, timeout=_TIMEOUT,
        exc=ErrorBCRA, reintentos=_MAX_REINTENTOS, throttle=_throttle,
    )


def catalogo() -> list[dict]:
    """Las ~1.581 variables del catálogo (2 páginas). Campos por variable:
    idVariable, descripcion, categoria, tipoSerie, periodicidad, unidadExpresion,
    moneda, primerFechaInformada, ultFechaInformada, ultValorInformado."""
    out: list[dict] = []
    offset = 0
    while True:
        d = _get("/monetarias", {"limit": _PAGE, "offset": offset})
        rs = (d.get("metadata") or {}).get("resultset") or {}
        out.extend(d.get("results") or [])
        total = rs.get("count") or len(out)
        offset += _PAGE
        if offset >= total or not d.get("results"):
            break
    return out


def serie(id_variable: int, desde: str | None = None,
          hasta: str | None = None) -> list[dict]:
    """Puntos [{fecha, valor}] de una variable. Manda desde/hasta a la API y
    ADEMÁS re-filtra client-side (defensa si la API los ignora — REGLA #2: el
    comportamiento exacto de esos params se confirma con el diag del Droplet).
    Pagina si el resultado excede una página."""
    puntos: list[dict] = []
    offset = 0
    while True:
        params: dict = {"limit": _PAGE, "offset": offset}
        if desde:
            params["desde"] = desde
        if hasta:
            params["hasta"] = hasta
        d = _get(f"/monetarias/{id_variable}", params)
        page = [p for r in (d.get("results") or []) for p in (r.get("detalle") or [])]
        puntos.extend(page)
        rs = (d.get("metadata") or {}).get("resultset") or {}
        total = rs.get("count")
        offset += _PAGE
        if not page or total is None or offset >= total:
            break
    out = []
    for p in puntos:
        f, v = p.get("fecha"), p.get("valor")
        if not f or v is None:
            continue
        if desde and f < desde:
            continue
        if hasta and f > hasta:
            continue
        out.append({"fecha": f, "valor": float(v)})
    out.sort(key=lambda p: p["fecha"])
    return out
