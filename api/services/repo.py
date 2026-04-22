"""Capa de servicio — mercado repo (caución).

La caución es una operación a plazo sobre efectivo garantizada por títulos:
funcionalmente es un repo market, no un derivado. El motor `engines/caucion.py`
escribe el snapshot live (`Trading.CaucionSnapshot`) y vuelca el cierre
diario a `Trading.Caucion`.

Expone:
- `get_caucion(moneda)` — snapshot live (TTL 5 s).
- `get_historico_caucion(moneda, desde, hasta)` — cierre histórico.
"""
from __future__ import annotations

from api.cache import cached
from api.db import get_db_trading


@cached(ttl=5)
def get_caucion(moneda: str | None = None) -> list:
    """Snapshot live de caución (Trading.CaucionSnapshot).

    Devuelve 1 o 2 docs: con `moneda` filtra solo esa, sin filtro devuelve ambas.
    Cada doc trae TNA last/bid/offer/open/high/low/closing + plazo_dias + ticker.
    """
    db = get_db_trading()
    filtro: dict = {}
    if moneda:
        filtro["moneda"] = moneda.upper()
    return list(db["CaucionSnapshot"].find(filtro, {"_id": 0}))


@cached(ttl=300)
def get_historico_caucion(
    moneda: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
) -> list:
    """Serie histórica de caución cierre diario (Trading.Caucion)."""
    db = get_db_trading()
    filtro: dict = {}
    if moneda:
        filtro["moneda"] = moneda.upper()
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["fecha"] = rango
    return list(
        db["Caucion"]
        .find(filtro, {"_id": 0})
        .sort([("fecha", 1), ("moneda", 1)])
    )
