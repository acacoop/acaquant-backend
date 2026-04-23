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
    return list(_coll().find({}))


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

    Incluye al final los tickers de `config.TICKERS_EXTRA_PRECIOS` (que
    no están en Trading.Curvas) para que motor_rofex se suscriba live.
    motor_curvas no los toca porque sigue indexando solo desde Curvas.
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
    return base
