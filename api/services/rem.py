"""Expectativas REM (Relevamiento de Expectativas de Mercado, BCRA).

Lee de `Trading.REM`, que popula `jobs/argentina_datos._ingestar_rem` con
un filtro al indicador IPC nivel general INDEC y con el período ya
normalizado a 'YYYY-MM' en la ingesta.

3 funciones:

- `listar_informes`: meses con informe disponible.
- `expectativas`: serie cruda para un informe (mediana/promedio/percentiles).
- `breakeven_acumulado`: IPC mensual → promedio geométrico mensual acumulado
  desde hoy hasta cada mes futuro. Formato listo para superponer al BE de
  mercado en el chart.
"""
from __future__ import annotations

from datetime import date, timedelta

from api.cache import cached
from api.db import get_db_trading


def _resolver_informe(db, informe: str | None) -> str | None:
    """'ultimo' o vacío → el informe más reciente en Mongo."""
    if informe and informe != "ultimo":
        return informe
    doc = db["REM"].find_one({}, sort=[("informe", -1)], projection={"informe": 1})
    return doc["informe"] if doc else None


def _fin_de_mes(yyyy_mm: str) -> date | None:
    """'2026-04' → date(2026, 4, 30)."""
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
    """Diagnóstico rápido de Trading.REM."""
    db = get_db_trading()
    total = db["REM"].estimated_document_count()  # O(1) por metadata, no COLLSCAN
    informes = sorted(db["REM"].distinct("informe"), reverse=True)
    ultimo = informes[0] if informes else None
    sample_periodos: list[str] = []
    n_mensual = 0
    if ultimo:
        n_mensual = db["REM"].count_documents(
            {"informe": ultimo, "periodo_tipo": "mensual"},
        )
        sample_periodos = [
            r["periodo"] for r in db["REM"].find(
                {"informe": ultimo, "periodo_tipo": "mensual"},
                {"_id": 0, "periodo": 1},
            ).sort("periodo", 1).limit(8)
        ]
    return {
        "total_docs":         total,
        "n_informes":         len(informes),
        "ultimo_informe":     ultimo,
        "informes":           informes[:15],
        "n_mensuales_ultimo": n_mensual,
        "sample_periodos":    sample_periodos,
    }


@cached(ttl=300)
def listar_informes() -> list[dict]:
    """Informes disponibles, ordenados desc."""
    db = get_db_trading()
    pipeline = [
        {"$group": {"_id": "$informe", "n": {"$sum": 1}}},
        {"$sort": {"_id": -1}},
    ]
    return [
        {"informe": r["_id"], "n_registros": r["n"]}
        for r in db["REM"].aggregate(pipeline)
        if r["_id"]
    ]


@cached(ttl=300)
def expectativas(
    informe: str | None = None,
    periodo_tipo: str | None = None,
    periodo_desde: str | None = None,
    periodo_hasta: str | None = None,
) -> dict:
    """Serie cruda de expectativas IPC por período dentro de un informe.

    Devuelve ordenado por período asc (funciona lexicográfico porque el
    campo ya está normalizado a 'YYYY-MM' en la ingesta).
    """
    db = get_db_trading()
    inf = _resolver_informe(db, informe)
    if inf is None:
        return {"informe": None, "items": []}

    filtro: dict = {"informe": inf}
    if periodo_tipo:
        filtro["periodo_tipo"] = periodo_tipo
    if periodo_desde:
        filtro["periodo"] = {"$gte": periodo_desde}
    if periodo_hasta:
        filtro.setdefault("periodo", {})["$lte"] = periodo_hasta

    proj = {
        "_id": 0, "periodo": 1, "periodo_tipo": 1,
        "mediana": 1, "promedio": 1, "desvio": 1,
        "minimo": 1, "maximo": 1,
        "p10": 1, "p25": 1, "p75": 1, "p90": 1,
        "participantes": 1,
    }
    items = list(db["REM"].find(filtro, proj).sort("periodo", 1))
    return {"informe": inf, "items": items}


@cached(ttl=300)
def breakeven_acumulado(informe: str | None = None) -> dict:
    """IPC mensual del REM → promedio mensual geométrico acumulado desde HOY
    hasta cada mes futuro. Para comparar contra BE de mercado (también
    promedio mensual acumulado).

        ipc_mensual_i = mediana_i / 100  para i = 1..k
        acum_k         = prod_{i=1..k} (1 + ipc_mensual_i) − 1
        mensual_prom_k = (1 + acum_k) ^ (1/k) − 1

    Output: `[{periodo, fin_mes, ipc_mensual_rem, promedio_mensual_acum}, ...]`
    """
    db = get_db_trading()
    inf = _resolver_informe(db, informe)
    if inf is None:
        return {"informe": None, "serie": []}

    hoy_key = date.today().strftime("%Y-%m")
    # Solo meses >= mes actual (comparación lexicográfica funciona con YYYY-MM).
    items = list(db["REM"].find(
        {"informe": inf, "periodo_tipo": "mensual", "periodo": {"$gte": hoy_key}},
        {"_id": 0, "periodo": 1, "mediana": 1},
    ).sort("periodo", 1))

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
