"""Expectativas REM (Relevamiento de Expectativas de Mercado, BCRA).

Lee de `Trading.REM` que popula `jobs/argentina_datos._ingestar_rem`.
Expone 3 funciones:

- `listar_informes`: meses con informe disponible en Mongo.
- `expectativas`: serie cruda para un indicador / informe / rango de período.
- `breakeven_acumulado`: el indicador IPC mensual del REM convertido a
  promedio geométrico MENSUAL acumulado desde hoy hasta cada mes futuro.
  Útil para comparar contra el breakeven de mercado (BE de Lecap = promedio
  mensual acumulado implícito hasta el vencimiento).
"""
from __future__ import annotations

from datetime import date, timedelta

from api.cache import cached
from api.db import get_db_trading


def _resolver_indicador_ipc(db, informe: str | None) -> str | None:
    """Busca en el informe el indicador de IPC mensual sin depender del
    string exacto.

    La API del BCRA usa labels humanos que pueden variar entre informes
    ('IPC nivel general', 'IPC Nacional Nivel General', etc). Buscamos
    case-insensitive algo que contenga 'IPC' Y alguna forma de 'nivel
    general' o simplemente 'general'. Fallback al primer indicador con
    periodo_tipo='mensual' si nada matchea.
    """
    filtro: dict = {"periodo_tipo": "mensual"}
    if informe:
        filtro["informe"] = informe
    candidatos = db["REM"].distinct("indicador", filtro)
    if not candidatos:
        return None

    def _score(nombre: str) -> int:
        low = nombre.lower()
        if "ipc" in low and ("nivel general" in low or "general" in low):
            return 3
        if "ipc" in low and "núcleo" not in low and "nucleo" not in low:
            return 2
        if "inflación" in low or "inflacion" in low:
            return 1
        return 0

    ranked = sorted(candidatos, key=_score, reverse=True)
    return ranked[0]


@cached(ttl=300)
def listar_informes() -> list[dict]:
    """Devuelve los informes disponibles en Mongo ordenados desc.

    Output: `[{informe: '2026-03', n_registros: 320}, ...]`
    """
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


def _resolver_informe(db, informe: str | None) -> str | None:
    """'ultimo' o vacío → el informe máximo presente en Mongo."""
    if informe and informe != "ultimo":
        return informe
    doc = db["REM"].find_one({}, sort=[("informe", -1)], projection={"informe": 1})
    return doc["informe"] if doc else None


@cached(ttl=300)
def expectativas(
    indicador: str | None = None,
    informe: str | None = None,
    periodo_tipo: str | None = None,
    periodo_desde: str | None = None,
    periodo_hasta: str | None = None,
) -> dict:
    """Serie cruda de expectativas para un indicador.

    Defaults: indicador = IPC mensual (auto-detectado por fuzzy match sobre
    los indicadores presentes), informe = último disponible.
    Devuelve ordenado por período asc.
    """
    db = get_db_trading()
    inf = _resolver_informe(db, informe)
    ind = indicador or _resolver_indicador_ipc(db, inf)
    if inf is None or ind is None:
        return {"informe": inf, "indicador": ind, "items": []}

    filtro: dict = {"informe": inf, "indicador": ind}
    if periodo_tipo:
        filtro["periodo_tipo"] = periodo_tipo
    if periodo_desde:
        filtro["periodo"] = {"$gte": periodo_desde}
    if periodo_hasta:
        filtro.setdefault("periodo", {})["$lte"] = periodo_hasta

    proj = {
        "_id": 0, "periodo": 1, "periodo_tipo": 1,
        "periodo_desde": 1, "periodo_hasta": 1,
        "mediana": 1, "promedio": 1, "desvio": 1,
        "minimo": 1, "maximo": 1,
        "p10": 1, "p25": 1, "p75": 1, "p90": 1,
        "participantes": 1, "unidad": 1,
    }
    items = list(db["REM"].find(filtro, proj).sort("periodo", 1))
    return {"informe": inf, "indicador": ind, "items": items}


@cached(ttl=300)
def breakeven_acumulado(
    informe: str | None = None,
    indicador: str | None = None,
) -> dict:
    """Convierte el IPC mensual del REM en el promedio mensual geométrico
    acumulado desde HOY hasta cada mes futuro, para comparar vs breakeven
    de mercado.

    Para el mes_k del REM (k = 1, 2, ..., N meses adelante):
        ipc_mensual_i  = REM[mes_i].mediana / 100  para i = 1..k
        acum_k         = prod_{i=1..k} (1 + ipc_mensual_i) − 1
        mensual_prom_k = (1 + acum_k) ^ (1 / k) − 1

    Output: `[{periodo, fin_mes, ipc_mensual_rem, promedio_mensual_acum}, ...]`
    donde `fin_mes` es date ISO del último día del mes (para plotear contra
    vencimientos de Lecap, que son a fin de mes típicamente).
    """
    db = get_db_trading()
    inf = _resolver_informe(db, informe)
    ind = indicador or _resolver_indicador_ipc(db, inf)
    if inf is None or ind is None:
        return {"informe": inf, "indicador": ind, "serie": []}

    hoy = date.today()
    items = list(db["REM"].find(
        {"informe": inf, "indicador": ind, "periodo_tipo": "mensual"},
        {"_id": 0, "periodo": 1, "mediana": 1, "periodo_hasta": 1},
    ).sort("periodo", 1))

    # Filtrar a meses futuros (periodo >= mes actual).
    mes_actual_key = hoy.strftime("%Y-%m")
    futuros = [r for r in items if str(r.get("periodo", ""))[:7] >= mes_actual_key]

    serie: list[dict] = []
    factor_acum = 1.0
    n = 0
    for r in futuros:
        med = r.get("mediana")
        if med is None:
            continue
        m_pct = float(med) / 100.0
        factor_acum *= (1 + m_pct)
        n += 1
        prom_mensual_geom = factor_acum ** (1 / n) - 1
        periodo_key = str(r["periodo"])[:7]
        fin_mes = _fin_de_mes(periodo_key)
        serie.append({
            "periodo":              periodo_key,
            "fin_mes":              fin_mes.isoformat() if fin_mes else None,
            "ipc_mensual_rem":      round(m_pct, 6),
            "promedio_mensual_acum": round(prom_mensual_geom, 6),
            "meses_acumulados":     n,
        })
    return {"informe": inf, "indicador": ind, "serie": serie}


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
