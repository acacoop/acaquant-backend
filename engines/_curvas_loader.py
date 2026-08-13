"""Carga común del master de renta fija para todos los motores.

La definición estática de instrumentos vive en `mercado.curvas` (Postgres). El `data`
jsonb es el doc completo (mismo shape) → cero cambio para los motores que
consumen estos docs. Este módulo unifica el acceso vía `core.curvas_sql`.
"""
from __future__ import annotations

from core import curvas_sql


def cargar_todos() -> list[dict]:
    """Todos los docs del master (sin filtros)."""
    return curvas_sql.cargar_todos()


def cargar_por_curva() -> dict[str, list[dict]]:
    """Docs agrupados por el campo `curva`. Ignora docs sin `curva`.

    Usado por forwards y breakevens para separar tasa_fija / cer / etc.
    """
    return curvas_sql.agrupado_por_curva()


def cargar_indexado_por_ticker() -> dict[str, dict]:
    """Dict ticker → doc completo. Ignora docs sin `ticker`.

    Usado por curvas para enriquecer trades de TimeSales.
    """
    return curvas_sql.indexado_por_ticker()


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
    docs = [d for d in curvas_sql.cargar_todos() if d.get("ticker")]
    docs.sort(key=lambda d: d.get("fecha_vencimiento") or "")
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
