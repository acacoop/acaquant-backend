"""api/services/rem_sql.py — Expectativas REM (IPC INDEC, BCRA) leyendo Postgres.

Espejo SQL-native de `api/services/rem.py`. Fuente: `macro.rem` (1:1 con
`Trading.REM` vía `jobs/sync_postgres.sync_rem`; medido 98=98). Mismo shape de
salida que el path Mongo (mismas claves, mismo orden de período asc) para que el
dual-run por flag `REM_SQL` no tenga drift.

Tipos: las columnas son `numeric` → psycopg devuelve `Decimal`; se castean a
`float` (Mongo los devuelve float). Son todos números que alimentan un chart →
float es lo correcto para el consumidor; el gate compara por valor, no por tipo.

Gate de paridad: `scripts/compare_rem_sql_vs_mongo.py` (read-only, corre en el
Droplet contra ambas bases). NO migrar la lectura sin verde del comparador.
"""
from __future__ import annotations

from datetime import date

from psycopg.rows import dict_row

from api.cache import cached
from api.services.rem import _fin_de_mes  # pura (no toca Mongo) → se reusa
from core.postgres import get_pool


def _q(sql: str, params: tuple | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def _f(v) -> float | None:
    """Decimal/numeric → float; None pasa. Mongo emite float."""
    return float(v) if v is not None else None


def _resolver_informe(informe: str | None) -> str | None:
    """'ultimo' o vacío → el informe más reciente (max lexicográfico = el path Mongo)."""
    if informe and informe != "ultimo":
        return informe
    rows = _q("SELECT informe FROM macro.rem ORDER BY informe DESC LIMIT 1")
    return rows[0]["informe"] if rows else None


@cached(ttl=60)
def debug_info() -> dict:
    """Diagnóstico de macro.rem — espejo del path Mongo."""
    total = _q("SELECT count(*) AS n FROM macro.rem")[0]["n"]
    informes = [r["informe"] for r in
                _q("SELECT DISTINCT informe FROM macro.rem ORDER BY informe DESC")]
    ultimo = informes[0] if informes else None
    n_mensual = 0
    sample_periodos: list[str] = []
    if ultimo:
        n_mensual = _q(
            "SELECT count(*) AS n FROM macro.rem WHERE informe = %s AND periodo_tipo = 'mensual'",
            (ultimo,),
        )[0]["n"]
        sample_periodos = [
            r["periodo"] for r in _q(
                "SELECT periodo FROM macro.rem WHERE informe = %s AND periodo_tipo = 'mensual' "
                "ORDER BY periodo ASC LIMIT 8", (ultimo,),
            )
        ]
    return {
        "total_docs":         int(total),
        "n_informes":         len(informes),
        "ultimo_informe":     ultimo,
        "informes":           informes[:15],
        "n_mensuales_ultimo": int(n_mensual),
        "sample_periodos":    sample_periodos,
    }


@cached(ttl=300)
def listar_informes() -> list[dict]:
    """Informes disponibles, ordenados desc (espejo del $group Mongo)."""
    rows = _q("SELECT informe, count(*) AS n FROM macro.rem "
              "GROUP BY informe ORDER BY informe DESC")
    return [{"informe": r["informe"], "n_registros": int(r["n"])}
            for r in rows if r["informe"]]


@cached(ttl=300)
def expectativas(
    informe: str | None = None,
    periodo_tipo: str | None = None,
    periodo_desde: str | None = None,
    periodo_hasta: str | None = None,
) -> dict:
    """Serie cruda de expectativas IPC por período dentro de un informe, asc.

    Mismas claves que el path Mongo: periodo, periodo_tipo, mediana, promedio,
    desvio, minimo, maximo, p10, p25, p75, p90, participantes.
    """
    inf = _resolver_informe(informe)
    if inf is None:
        return {"informe": None, "items": []}

    where = ["informe = %s"]
    params: list = [inf]
    if periodo_tipo:
        where.append("periodo_tipo = %s")
        params.append(periodo_tipo)
    if periodo_desde:
        where.append("periodo >= %s")
        params.append(periodo_desde)
    if periodo_hasta:
        where.append("periodo <= %s")
        params.append(periodo_hasta)

    rows = _q(
        "SELECT periodo, periodo_tipo, mediana, promedio, desvio, minimo, maximo, "
        "p10, p25, p75, p90, participantes FROM macro.rem "
        f"WHERE {' AND '.join(where)} ORDER BY periodo ASC",
        tuple(params),
    )
    items = [{
        "periodo":       r["periodo"],
        "periodo_tipo":  r["periodo_tipo"],
        "mediana":       _f(r["mediana"]),
        "promedio":      _f(r["promedio"]),
        "desvio":        _f(r["desvio"]),
        "minimo":        _f(r["minimo"]),
        "maximo":        _f(r["maximo"]),
        "p10":           _f(r["p10"]),
        "p25":           _f(r["p25"]),
        "p75":           _f(r["p75"]),
        "p90":           _f(r["p90"]),
        "participantes": _f(r["participantes"]),
    } for r in rows]
    return {"informe": inf, "items": items}


@cached(ttl=300)
def breakeven_acumulado(informe: str | None = None) -> dict:
    """IPC mensual del REM → promedio mensual geométrico acumulado desde HOY.
    Misma fórmula y mismo redondeo (6 dec) que el path Mongo."""
    inf = _resolver_informe(informe)
    if inf is None:
        return {"informe": None, "serie": []}

    hoy_key = date.today().strftime("%Y-%m")
    items = _q(
        "SELECT periodo, mediana FROM macro.rem "
        "WHERE informe = %s AND periodo_tipo = 'mensual' AND periodo >= %s "
        "ORDER BY periodo ASC", (inf, hoy_key),
    )

    serie: list[dict] = []
    factor_acum = 1.0
    n = 0
    for r in items:
        med = r.get("mediana")
        if med is None:
            continue
        m_pct = float(med) / 100.0
        factor_acum *= (1 + m_pct)
        n += 1
        prom_mensual_geom = factor_acum ** (1 / n) - 1
        yyyy_mm = r["periodo"]
        fin_mes = _fin_de_mes(yyyy_mm)
        serie.append({
            "periodo":               yyyy_mm,
            "fin_mes":               fin_mes.isoformat() if fin_mes else None,
            "ipc_mensual_rem":       round(m_pct, 6),
            "promedio_mensual_acum": round(prom_mensual_geom, 6),
            "meses_acumulados":      n,
        })
    return {"informe": inf, "serie": serie}
