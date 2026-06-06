"""Carga común de Trading.Curvas para todos los motores.

La definición estática de instrumentos vive en Trading.Curvas. Antes cada
motor tenía su propia función de carga; las 4 hacían queries casi idénticas
con micro-diferencias accidentales (proyecciones distintas, filtros
distintos, shape de retorno distinto). Este módulo unifica ese acceso:
cualquier cambio en el shape de Trading.Curvas se refleja tocando un
único lugar.

Los motores corren siempre desde procesos con el singleton de Mongo ya
inicializado (core/mongo.py), así que las funciones no reciben `client`:
usan el singleton directamente.
"""
from __future__ import annotations

from core.mongo import get_mongo_client


def _coll():
    return get_mongo_client()["Trading"]["Curvas"]


def cargar_todos() -> list[dict]:
    """Todos los docs de Trading.Curvas sin filtros ni projection."""
    return list(_coll().find({}))  # perf-ok: PERF001 — Curvas (~57 docs), se necesitan los flujos completos


def cargar_por_curva() -> dict[str, list[dict]]:
    """Docs agrupados por el campo `curva`. Ignora docs sin `curva`.

    Usado por forwards y breakevens para separar tasa_fija / cer / etc.
    """
    grupos: dict[str, list[dict]] = {}
    for d in cargar_todos():
        curva = d.get("curva")
        if curva:
            grupos.setdefault(curva, []).append(d)
    return grupos


def cargar_indexado_por_ticker() -> dict[str, dict]:
    """Dict ticker → doc completo. Ignora docs sin `ticker`.

    Usado por curvas para enriquecer trades de TimeSales.
    """
    return {d["ticker"]: d for d in cargar_todos() if d.get("ticker")}


def cargar_tickers_ordenados() -> list[str]:
    """Tickers ordenados por `fecha_vencimiento` ascendente.

    Docs sin `fecha_vencimiento` ordenan primero (string vacío). Ignora
    docs sin `ticker`. Usa projection para minimizar payload.

    Incluye al final, en este orden:
      1. `config.TICKERS_EXTRA_PRECIOS` (tickers fijos editados a mano).
      2. `Trading.AdhocSubscriptions` (tickers pedidos en runtime desde
         el Dashboard de Operar, TTL 7d). motor_rofex los suscribe live;
         motor_curvas los ignora (no están en Trading.Curvas).
    """
    docs = list(_coll().find(
        {"ticker": {"$exists": True}},
        {"_id": 0, "ticker": 1, "fecha_vencimiento": 1},
    ))
    docs.sort(key=lambda d: d.get("fecha_vencimiento", ""))
    base = [d["ticker"] for d in docs if d.get("ticker")]

    try:
        from config import TICKERS_EXTRA_PRECIOS
    except ImportError:
        TICKERS_EXTRA_PRECIOS = []

    base_set = set(base)
    for tk in TICKERS_EXTRA_PRECIOS:
        if tk and tk not in base_set:
            base.append(tk)
            base_set.add(tk)

    # AdhocSubscriptions — tickers que el motor adoptó dinámicamente.
    try:
        from core.adhoc_subscriptions import list_active_tickers
        for tk in list_active_tickers():
            if tk and tk not in base_set:
                base.append(tk)
                base_set.add(tk)
    except Exception:
        # Si la colección no existe aún (primer deploy), seguimos.
        pass

    return base
