"""Cache in-process para endpoints FastAPI.

Decorador `@cached(ttl=N)` para handlers síncronos. Llave = nombre fn +
argumentos NORMALIZADOS contra la firma (`inspect.signature().bind()`), así que
`f(3)`, `f(x=3)` y `f()` con `x=3` por default son la MISMA entrada de cache.
No requiere Redis: el api.service es un único proceso, así que un dict con TTL
alcanza y elimina 1 round-trip a la DB por request repetido dentro de la ventana.

El `_store` está acotado: sweep de expiradas + LRU-eviction al pasar
`_MAX_ENTRIES`. Antes de esto cada combinación única de kwargs se quedaba
en el dict para siempre — en producción con queries diversas (filtros por
fecha, contraparte, ticker) crecía monotónicamente y era el leak principal
del proceso (incidente 2026-05-05: RAM pasaba 60% del droplet a las 24h).

**Anti-estampida (single-flight, AUDITORIA M4)**: cuando un TTL vence, sin
protección los N requests concurrentes para esa llave ven el cache vacío a la
vez y los N ejecutan la MISMA query pesada contra el M10 en el mismo segundo
(estampida — una de las puertas por las que volvió el CPU 100%). Con el
registro `_inflight`, el PRIMER request que entra a computar una llave registra
un Event; los demás esperan ese Event y reusan el resultado. Mil usuarios le
cuestan a la base lo mismo que uno.
"""
from __future__ import annotations

import functools
import inspect
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

# Límite duro de entradas. Las vistas parametrizadas por cuenta/operador multiplican
# combinaciones (n_usuarios × n_filtros), así que 512 evictaba entradas VIVAS justo
# con carga alta. Son referencias a resultados ya en memoria: subir el cap es barato.
# Cuando se sobrepasa, evictamos por LRU (la entrada accedida hace más tiempo se cae).
_MAX_ENTRIES = 2048

# Tope de espera de un waiter por el cómputo del líder. Si el líder tarda más
# que esto (query muy lenta), el waiter cae a computar él mismo en vez de
# colgarse — degradación segura, nunca deadlock. 30s alinea con el timeout
# típico de request del frontend.
_INFLIGHT_TIMEOUT_S = 30.0

_lock = threading.Lock()
# OrderedDict para tracking LRU: move_to_end al leer / escribir, popitem(last=False)
# para evict del menos reciente.
_store: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()
# Llaves en cómputo AHORA → Event que se setea cuando termina. Protegido por
# `_lock` (mismo lock del store: las operaciones son O(1), no hay contención).
_inflight: dict[tuple, threading.Event] = {}


def _sweep_expired_locked(now: float) -> None:
    """Quita entradas con TTL vencido. Asume `_lock` tomado."""
    expired = [k for k, (exp, _) in _store.items() if exp <= now]
    for k in expired:
        _store.pop(k, None)


def invalidate(*fn_names: str) -> int:
    """Borra del cache las entradas de las funciones nombradas (post-mutación).
    Llave = (módulo, nombre_fn, kwargs) → matchea por nombre_fn."""
    with _lock:
        keys = [k for k in _store if k[1] in fn_names]
        for k in keys:
            _store.pop(k, None)
    return len(keys)


def cached(ttl: int) -> Callable:
    """Cachea la respuesta de un handler por `ttl` segundos.

    Usa todos los argumentos del handler como parte de la llave, así filtros
    distintos se cachean por separado. Los argumentos se normalizan contra la
    firma (posicionales → nombre, defaults aplicados): llamar `f(3)` o `f(x=3)`
    da la MISMA llave. Los valores de los argumentos tienen que ser hasheables.
    """
    def decorator(fn: Callable) -> Callable:
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            bound = sig.bind(*args, **kwargs)   # TypeError si la llamada no matchea la firma
            bound.apply_defaults()
            key = (fn.__module__, fn.__name__, tuple(sorted(bound.arguments.items())))
            now = time.time()
            with _lock:
                entry = _store.get(key)
                if entry and entry[0] > now:
                    _store.move_to_end(key)  # marca como recién usada
                    return entry[1]
                # Cache miss. ¿Hay alguien ya computando esta llave?
                ev = _inflight.get(key)
                soy_lider = ev is None
                if soy_lider:
                    ev = threading.Event()
                    _inflight[key] = ev

            if not soy_lider:
                # Otro thread ya está computando esta misma llave: esperamos su
                # resultado en vez de disparar la query de nuevo.
                ev.wait(timeout=_INFLIGHT_TIMEOUT_S)
                with _lock:
                    entry = _store.get(key)
                    if entry and entry[0] > time.time():
                        _store.move_to_end(key)
                        return entry[1]
                # El líder no dejó valor (resultado vacío, excepción o timeout):
                # caemos a computar nosotros — best-effort, sin re-registrar.
                return fn(*args, **kwargs)

            # Somos el líder: computamos y despertamos a los waiters al final.
            try:
                result = fn(*args, **kwargs)
            except Exception:
                with _lock:
                    _inflight.pop(key, None)
                ev.set()
                raise

            # Negative caching off: no cacheamos respuestas vacías. Suelen
            # indicar error transitorio (cluster pausado, query caída) y
            # cachearlas propaga el estado vacío durante TTL segundos.
            if result is None or result == [] or result == {}:
                with _lock:
                    _inflight.pop(key, None)
                ev.set()
                return result

            with _lock:
                _sweep_expired_locked(now)
                _store[key] = (now + ttl, result)
                _store.move_to_end(key)
                # Cap duro: si seguimos pasados, evict de la entrada más vieja.
                while len(_store) > _MAX_ENTRIES:
                    _store.popitem(last=False)
                _inflight.pop(key, None)
            ev.set()
            return result
        return wrapper
    return decorator


def clear_cache() -> int:
    """Vacía el cache. Devuelve cuántas entradas se purgaron."""
    with _lock:
        n = len(_store)
        _store.clear()
        # Despertamos cualquier waiter en vuelo para que no se cuelgue su timeout.
        for ev in _inflight.values():
            ev.set()
        _inflight.clear()
    return n


def cache_size() -> int:
    """Cantidad actual de entradas — útil para monitoring/diagnóstico."""
    with _lock:
        return len(_store)
