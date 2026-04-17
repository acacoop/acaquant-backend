"""Cache in-process para endpoints FastAPI.

Decorador `@cached(ttl=N)` para handlers síncronos. Llave = nombre fn + kwargs.
No requiere Redis: el api.service es un único proceso, así que un dict con TTL
alcanza y elimina 1 round-trip a Mongo por request repetido dentro de la ventana.
"""
from __future__ import annotations

import functools
import threading
import time
from collections.abc import Callable
from typing import Any

_lock = threading.Lock()
_store: dict[tuple, tuple[float, Any]] = {}


def cached(ttl: int) -> Callable:
    """Cachea la respuesta de un handler por `ttl` segundos.

    Usa todos los kwargs del handler como parte de la llave, así filtros
    distintos se cachean por separado. Los args posicionales no son soportados
    (los handlers de FastAPI los reciben como kwargs).
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(**kwargs):
            key = (fn.__module__, fn.__name__, tuple(sorted(kwargs.items())))
            now = time.time()
            with _lock:
                entry = _store.get(key)
                if entry and entry[0] > now:
                    return entry[1]
            result = fn(**kwargs)
            with _lock:
                _store[key] = (now + ttl, result)
            return result
        return wrapper
    return decorator


def clear_cache() -> int:
    """Vacía el cache. Devuelve cuántas entradas se purgaron."""
    with _lock:
        n = len(_store)
        _store.clear()
    return n
