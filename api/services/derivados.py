"""Capa de servicio — derivados (futuros DLR, forwards, breakevens).

Reúne las magnitudes derivadas de la curva local y los únicos derivados
puros (futuros DLR de ROFEX). Separación por dominio vs `renta_fija.py`:
allá viven los cash bonds; acá todo lo que se construye a partir de ellos.

Fuentes:
- Futuros DLR      → engines/futuros_dlr.py
- Forwards         → engines/forwards.py
- Breakevens       → engines/breakevens.py
"""
from __future__ import annotations

from datetime import date

from api.cache import cached
from api.db import get_db_trading

# ─────────────────────────────────────────────────────────────────────────────
# Futuros DLR (Trading.FuturosDLRSnapshot + FuturosDLR)
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=5)
def get_futuros_dlr() -> list:
    """Snapshot live de outrights DLR vigentes (Trading.FuturosDLRSnapshot).

    Filtra contratos cuyo vencimiento ya pasó. El motor escribe con
    ReplaceOne(upsert) y nunca borra; al vencer un contrato el doc queda
    fantasma con la última info que tenía. Sin este filtro la API devuelve
    contratos vencidos con TNAs sin sentido (jobs/cleanup_futuros_dlr.py
    se encarga del housekeeping nocturno).
    """
    db = get_db_trading()
    hoy = date.today().strftime("%Y%m%d")
    return list(
        db["FuturosDLRSnapshot"]
        .find({"vencimiento": {"$gt": hoy}}, {"_id": 0})
        .sort("vencimiento", 1)
    )


@cached(ttl=300)
def get_historico_futuros_dlr(
    ticker: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
) -> list:
    """Cierre histórico (Trading.FuturosDLR). Filtrable por ticker y rango."""
    db = get_db_trading()
    filtro: dict = {}
    if ticker:
        filtro["ticker"] = ticker
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["fecha"] = rango
    return list(
        db["FuturosDLR"]
        .find(filtro, {"_id": 0})
        .sort([("fecha", 1), ("vencimiento", 1)])
    )


# ─────────────────────────────────────────────────────────────────────────────
# Forwards (Trading.ForwardsLive + ForwardsHistorico)
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=30)
def get_forwards(curva: str | None = None) -> list:
    db = get_db_trading()
    filtro: dict = {}
    if curva:
        filtro["curva"] = curva
    return list(db["ForwardsLive"].find(filtro, {"_id": 0}))


@cached(ttl=300)
def get_historico_forwards(
    curva: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
) -> list:
    db = get_db_trading()
    filtro: dict = {}
    if curva:
        filtro["curva"] = curva
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["fecha"] = rango
    return list(db["ForwardsHistorico"].find(filtro, {"_id": 0}))


# Coeficientes (media, desvío) por par de forwards. Se actualizan 1x/día por
# jobs/forwards_zscore.py. El front computa z = (live − media) / desvío en
# cada tick — por eso TTL corto: cuando llega el cierre, el front debe verlo.
@cached(ttl=60)
def get_forwards_zscore(curva: str | None = None) -> list:
    db = get_db_trading()
    filtro: dict = {}
    if curva:
        filtro["curva"] = curva
    return list(db["ForwardsZscore"].find(filtro, {"_id": 0}))


# ─────────────────────────────────────────────────────────────────────────────
# Breakevens (Trading.BreakevensLive + BreakevensHistorico)
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=30)
def get_breakevens() -> list:
    db = get_db_trading()
    return list(db["BreakevensLive"].find({}, {"_id": 0}))


@cached(ttl=300)
def get_historico_breakevens(desde: str | None = None, hasta: str | None = None) -> list:
    db = get_db_trading()
    filtro: dict = {}
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["fecha"] = rango
    return list(db["BreakevensHistorico"].find(filtro, {"_id": 0}))
