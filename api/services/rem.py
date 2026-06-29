"""Expectativas REM (Relevamiento de Expectativas de Mercado, BCRA).

SQL-native (decomiso Mongo): `Trading.REM` fue dropeada — la fuente es `macro.rem`
(Postgres). Este módulo delega TODO en el twin `rem_sql`; se mantiene por compat con
los call-sites (router selector + MCP + descomposicion_retorno).

3 funciones: `listar_informes` (meses con informe), `expectativas` (serie cruda por
informe), `breakeven_acumulado` (IPC mensual → promedio geométrico acumulado).
"""
from __future__ import annotations

from datetime import date, timedelta

from api.cache import cached


def _fin_de_mes(yyyy_mm: str) -> date | None:
    """'2026-04' → date(2026, 4, 30). Helper PURO (sin DB) reusado por rem_sql."""
    if not yyyy_mm or len(yyyy_mm) < 7:
        return None
    try:
        y = int(yyyy_mm[:4])
        m = int(yyyy_mm[5:7])
    except ValueError:
        return None
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return nxt - timedelta(days=1)


@cached(ttl=60)
def debug_info() -> dict:
    from api.services import rem_sql
    return rem_sql.debug_info()


@cached(ttl=300)
def listar_informes() -> list[dict]:
    from api.services import rem_sql
    return rem_sql.listar_informes()


@cached(ttl=300)
def expectativas(
    informe: str | None = None,
    periodo_tipo: str | None = None,
    periodo_desde: str | None = None,
    periodo_hasta: str | None = None,
) -> dict:
    from api.services import rem_sql
    return rem_sql.expectativas(
        informe=informe, periodo_tipo=periodo_tipo,
        periodo_desde=periodo_desde, periodo_hasta=periodo_hasta,
    )


@cached(ttl=300)
def breakeven_acumulado(informe: str | None = None) -> dict:
    from api.services import rem_sql
    return rem_sql.breakeven_acumulado(informe=informe)
