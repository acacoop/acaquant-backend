"""Cache in-process para endpoints FastAPI.

Decorador `@cached(ttl=N)` para handlers síncronos. Llave = nombre fn + kwargs.
No requiere Redis: el api.service es un único proceso, así que un dict con TTL
alcanza y elimina 1 round-trip a Mongo por request repetido dentro de la ventana.

El `_store` está acotado: sweep de expiradas + LRU-eviction al pasar
`_MAX_ENTRIES`. Antes de esto cada combinación única de kwargs se quedaba
en el dict para siempre — en producción con queries diversas (filtros por
fecha, contraparte, ticker) crecía monotónicamente y era el leak principal
del proceso (incidente 2026-05-05: RAM pasaba 60% del droplet a las 24h).
"""
from __future__ import annotations

import functools
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

# Límite duro de entradas. Con ~30 endpoints cacheados y ~10 combinaciones
# típicas de filtros, 512 deja buen margen. Cuando se sobrepasa, evictamos
# por LRU (la entrada accedida hace más tiempo se cae).
_MAX_ENTRIES = 512

_lock = threading.Lock()
# OrderedDict para tracking LRU: move_to_end al leer / escribir, popitem(last=False)
# para evict del menos reciente.
_store: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()


def _sweep_expired_locked(now: float) -> None:
    """Quita entradas con TTL vencido. Asume `_lock` tomado."""
    expired = [k for k, (exp, _) in _store.items() if exp <= now]
    for k in expired:
        _store.pop(k, None)


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
                    _store.move_to_end(key)  # marca como recién usada
                    return entry[1]
            result = fn(**kwargs)
            # Negative caching off: no cacheamos respuestas vacías. Suelen
            # indicar error transitorio (cluster pausado, query caída) y
            # cachearlas propaga el estado vacío durante TTL segundos.
            if result is None or result == [] or result == {}:
                return result
            with _lock:
                _sweep_expired_locked(now)
                _store[key] = (now + ttl, result)
                _store.move_to_end(key)
                # Cap duro: si seguimos pasados, evict de la entrada más vieja.
                while len(_store) > _MAX_ENTRIES:
                    _store.popitem(last=False)
            return result
        return wrapper
    return decorator


def clear_cache() -> int:
    """Vacía el cache. Devuelve cuántas entradas se purgaron."""
    with _lock:
        n = len(_store)
        _store.clear()
    return n


def cache_size() -> int:
    """Cantidad actual de entradas — útil para monitoring/diagnóstico."""
    with _lock:
        return len(_store)
