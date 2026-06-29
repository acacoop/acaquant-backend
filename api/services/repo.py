"""Capa de servicio — mercado repo (caución).

SQL-native (decomiso Mongo): `Trading.Caucion`/`CaucionSnapshot` fueron dropeadas — el
motor `engines/caucion.py` escribe SQL (`mercado.caucion_snapshot` live + cierre en
`mercado.mercado_hist`). Este módulo delega en `mercado_hist_sql`; se mantiene por compat
con los call-sites (router selector + MCP).

Expone `get_caucion(moneda)` (snapshot live) y `get_historico_caucion(...)` (cierre histórico).
"""
from __future__ import annotations

from api.cache import cached


@cached(ttl=5)
def get_caucion(moneda: str | None = None) -> list:
    from api.services import mercado_hist_sql
    return mercado_hist_sql.get_caucion(moneda=moneda)


@cached(ttl=300)
def get_historico_caucion(
    moneda: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
) -> list:
    from api.services import mercado_hist_sql
    return mercado_hist_sql.get_historico_caucion(moneda=moneda, desde=desde, hasta=hasta)
