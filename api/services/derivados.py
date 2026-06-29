"""Capa de servicio — derivados (futuros DLR, forwards, breakevens).

SQL-native (decomiso Mongo): `Trading.{FuturosDLR(+Snapshot), Forwards*, Breakevens*}`
fueron dropeadas — los motores (futuros_dlr/forwards/breakevens) escriben SQL
(`mercado.futuros_dlr_snapshot` live + `mercado.mercado_hist` para histórico/live de
forwards/breakevens; `mercado.forwards_zscore`). Este módulo delega en `mercado_hist_sql`;
se mantiene por compat con los call-sites (router selector + MCP).
"""
from __future__ import annotations

from api.cache import cached


@cached(ttl=5)
def get_futuros_dlr() -> list:
    from api.services import mercado_hist_sql
    return mercado_hist_sql.get_futuros_dlr()


@cached(ttl=300)
def get_historico_futuros_dlr(
    ticker: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
) -> list:
    from api.services import mercado_hist_sql
    return mercado_hist_sql.get_historico_futuros_dlr(ticker=ticker, desde=desde, hasta=hasta)


@cached(ttl=30)
def get_forwards(curva: str | None = None) -> list:
    from api.services import mercado_hist_sql
    return mercado_hist_sql.get_forwards(curva=curva)


@cached(ttl=300)
def get_historico_forwards(
    curva: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
) -> list:
    from api.services import mercado_hist_sql
    return mercado_hist_sql.get_historico_forwards(curva=curva, desde=desde, hasta=hasta)


@cached(ttl=60)
def get_forwards_zscore(curva: str | None = None) -> list:
    from api.services import mercado_hist_sql
    return mercado_hist_sql.get_forwards_zscore(curva=curva)


@cached(ttl=30)
def get_breakevens() -> list:
    from api.services import mercado_hist_sql
    return mercado_hist_sql.get_breakevens()


@cached(ttl=300)
def get_historico_breakevens(desde: str | None = None, hasta: str | None = None) -> list:
    from api.services import mercado_hist_sql
    return mercado_hist_sql.get_historico_breakevens(desde=desde, hasta=hasta)
