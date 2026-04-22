"""Cliente de dolarapi.com — agregador público de tipos de dólar AR.

No requiere auth. Endpoints relevantes:
    GET /v1/dolares             → lista con todas las casas
    GET /v1/dolares/{casa}      → una casa específica

Schema de cada item:
    {
        "compra": float,          # bid
        "venta": float,           # ask
        "casa": str,              # "oficial" | "blue" | "bolsa" | "contadoconliqui" |
                                  # "mayorista" | "tarjeta" | "cripto"
        "nombre": str,            # "Dólar Oficial", etc.
        "moneda": str,            # "USD"
        "fechaActualizacion": str # ISO datetime
    }

Usamos solo `oficial`, `mayorista` y `blue` (MEP/CCL los calculamos con
nuestro motor via ROFEX, más preciso que el agregador).
"""
from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://dolarapi.com/v1/dolares"

# Casas soportadas hoy. Si querés sumar otra (bolsa, contadoconliqui, tarjeta,
# cripto), alcanza con agregar el slug acá y una fila en services/argy.py.
CASAS_SOPORTADAS: tuple[str, ...] = ("oficial", "mayorista", "blue")


class DolarApiError(RuntimeError):
    """Error genérico del cliente dolarapi."""


def _get(path: str, timeout: float = 10.0) -> Any:
    url = f"{_BASE_URL}{path}"
    try:
        r = requests.get(url, timeout=timeout)
    except requests.RequestException as e:
        raise DolarApiError(f"red falló en GET {path}: {e}") from e
    if r.status_code != 200:
        raise DolarApiError(f"{r.status_code} en {path}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError as e:
        raise DolarApiError(f"respuesta no-JSON en {path}: {e}") from e


def get_dolar(casa: str) -> dict:
    """Devuelve el dict de una `casa` específica (oficial / mayorista / blue / …).

    Raises DolarApiError si hay problema de red o la API responde != 200.
    """
    data = _get(f"/{casa}")
    if not isinstance(data, dict):
        raise DolarApiError(f"shape inesperada para /{casa}: {type(data).__name__}")
    return data


def get_todos() -> list[dict]:
    """Lista con todas las casas en una sola request."""
    data = _get("")
    if not isinstance(data, list):
        raise DolarApiError(f"shape inesperada para /: {type(data).__name__}")
    return data


def get_soportadas() -> list[dict]:
    """Filtra /v1/dolares a solo las casas que nos interesan."""
    todos = get_todos()
    return [d for d in todos if d.get("casa") in CASAS_SOPORTADAS]
