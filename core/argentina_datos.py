"""Cliente de argentinadatos.com — indicadores macro AR públicos.

Endpoints expuestos (GET, sin auth):
    /v1/finanzas/indices/riesgo-pais               → serie
    /v1/finanzas/indices/riesgo-pais/ultimo        → {fecha, valor}
    /v1/finanzas/indices/inflacion                 → IPC mensual serie
    /v1/finanzas/indices/inflacionInteranual       → YoY serie
    /v1/rems                                       → lista de meses publicados
    /v1/rems/ultimo                                → informe REM más reciente
    /v1/rems/{anio}/{mes}                          → informe específico

Schema de series simples: `[{fecha: "YYYY-MM-DD", valor: float}, ...]`.
Schema de REM: lista de indicadores con mediana/promedio/percentiles.

Rangos de sanity documentados acá; los aplican los jobs, no el cliente.
"""
from __future__ import annotations

import logging
from typing import Any

from core.http_base import get_json

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.argentinadatos.com"


class ArgDataError(RuntimeError):
    """Error genérico del cliente argentinadatos."""


def _get(path: str, timeout: float = 15.0) -> Any:
    return get_json(f"{_BASE_URL}{path}", timeout=timeout, exc=ArgDataError)


# ─────────────────────────────────────────────────────────────────────────────
# Riesgo país (EMBI+, serie diaria + último)
# ─────────────────────────────────────────────────────────────────────────────


def get_riesgo_pais_serie() -> list[dict]:
    data = _get("/v1/finanzas/indices/riesgo-pais")
    if not isinstance(data, list):
        raise ArgDataError(f"riesgo-pais: shape inesperada {type(data).__name__}")
    return data


def get_riesgo_pais_ultimo() -> dict:
    data = _get("/v1/finanzas/indices/riesgo-pais/ultimo")
    if not isinstance(data, dict):
        raise ArgDataError(f"riesgo-pais/ultimo: shape inesperada {type(data).__name__}")
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Inflación (IPC mensual + YoY)
# ─────────────────────────────────────────────────────────────────────────────


def get_inflacion_mensual() -> list[dict]:
    data = _get("/v1/finanzas/indices/inflacion")
    if not isinstance(data, list):
        raise ArgDataError(f"inflacion: shape inesperada {type(data).__name__}")
    return data


def get_inflacion_interanual() -> list[dict]:
    data = _get("/v1/finanzas/indices/inflacionInteranual")
    if not isinstance(data, list):
        raise ArgDataError(f"inflacionInteranual: shape inesperada {type(data).__name__}")
    return data


# ─────────────────────────────────────────────────────────────────────────────
# REM — relevamiento mensual BCRA
# ─────────────────────────────────────────────────────────────────────────────


def get_rems_meses() -> list[str]:
    """Lista de paths con los meses publicados, ej. ['/rems/2026/03', ...]."""
    data = _get("/v1/rems")
    if not isinstance(data, list):
        raise ArgDataError(f"rems: shape inesperada {type(data).__name__}")
    return data


def get_rems_ultimo() -> list[dict]:
    """Lista de dicts, cada uno un indicador del último informe REM."""
    data = _get("/v1/rems/ultimo")
    if not isinstance(data, list):
        raise ArgDataError(f"rems/ultimo: shape inesperada {type(data).__name__}")
    return data


def get_rems_mes(anio: int, mes: int) -> list[dict]:
    return _get(f"/v1/rems/{anio:04d}/{mes:02d}")
