"""Catálogo de tickers válidos para sugerencias `did_you_mean`.

Cuando el modelo pide un ticker que no existe (typo, confusión), la tool
devuelve un error con candidatos fuzzy-matched. Esto permite al modelo
auto-corregirse en el siguiente turn sin necesidad de que el usuario lo
haga manualmente.

Fuentes del catálogo:
- `Trading.Curvas.ticker_corto` (tasa fija, CER, TAMAR, dólar linked, soberanos)
- `Trading.BondsMaster.asset` (si se usa como fuente de Globales/Bonares)
- `Opciones.OptionsSnapshot.symbol` (opciones GGAL vigentes)

Cache en memoria con TTL (evita golpear Mongo en cada tool call).
"""
from __future__ import annotations

import difflib
import logging
import threading
import time

from core.mongo import get_mongo_client_read

logger = logging.getLogger(__name__)

_CACHE_TTL_S = 300  # 5 min
_cache: dict[str, object] = {"tickers": None, "loaded_at": 0.0}
_lock = threading.Lock()


def _load_from_mongo() -> set[str]:
    """Trae tickers válidos desde Mongo. Tolera fallos parciales."""
    tickers: set[str] = set()
    client = get_mongo_client_read()

    try:
        for tc in client["Trading"]["Curvas"].distinct("ticker_corto"):
            if tc:
                tickers.add(str(tc).upper())
    except Exception:
        logger.exception("ticker_catalog: fallo al traer Trading.Curvas")

    try:
        for a in client["Trading"]["BondsMaster"].distinct("asset"):
            if a:
                tickers.add(str(a).upper())
    except Exception:
        logger.exception("ticker_catalog: fallo al traer Trading.BondsMaster")

    try:
        for s in client["Opciones"]["OptionsSnapshot"].distinct("symbol"):
            if s:
                tickers.add(str(s).upper())
    except Exception:
        logger.exception("ticker_catalog: fallo al traer Opciones.OptionsSnapshot")

    return tickers


def get_valid_tickers(force_refresh: bool = False) -> set[str]:
    """Devuelve el set de tickers válidos, cacheado. Refresh cada 5 min."""
    now = time.time()
    with _lock:
        cached = _cache.get("tickers")
        loaded_at = _cache.get("loaded_at") or 0.0
        if (
            not force_refresh
            and cached
            and isinstance(cached, set)
            and (now - loaded_at) < _CACHE_TTL_S
        ):
            return cached

        tickers = _load_from_mongo()
        _cache["tickers"] = tickers
        _cache["loaded_at"] = now
        return tickers


def did_you_mean(ticker: str, n: int = 3, cutoff: float = 0.6) -> list[str]:
    """Sugerencias fuzzy para un ticker no encontrado.

    Args:
        ticker: string que el modelo pasó (posiblemente con typo).
        n: máximo de sugerencias.
        cutoff: similaridad mínima 0-1 (difflib ratio).

    Returns:
        Lista ordenada por cercanía (más probable primero). Vacía si ninguno pasa cutoff.
    """
    if not ticker:
        return []
    query = ticker.strip().upper()
    tickers = get_valid_tickers()
    if not tickers:
        return []
    return difflib.get_close_matches(query, tickers, n=n, cutoff=cutoff)
