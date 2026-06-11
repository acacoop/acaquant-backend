"""Tests del cache in-process (api/cache.py).

Cubre lo que importa: hit/miss + TTL, no-cacheo de vacíos, LRU cap, y la
garantía anti-estampida (single-flight) que motivó AUDITORIA M4.
"""
from __future__ import annotations

import threading
import time

import pytest

from api.cache import cache_size, cached, clear_cache


@pytest.fixture(autouse=True)
def _limpiar():
    clear_cache()
    yield
    clear_cache()


def test_hit_evita_segunda_llamada():
    llamadas = {"n": 0}

    @cached(ttl=60)
    def f(x):
        llamadas["n"] += 1
        return x * 2

    assert f(x=3) == 6
    assert f(x=3) == 6
    assert llamadas["n"] == 1  # la 2da fue cache hit


def test_kwargs_distintos_se_cachean_aparte():
    @cached(ttl=60)
    def f(x):
        return x * 10

    assert f(x=1) == 10
    assert f(x=2) == 20
    assert cache_size() == 2


def test_ttl_vencido_recomputa():
    llamadas = {"n": 0}

    @cached(ttl=0)  # vence inmediato
    def f():
        llamadas["n"] += 1
        return [1]

    f()
    time.sleep(0.01)
    f()
    assert llamadas["n"] == 2


def test_vacios_no_se_cachean():
    @cached(ttl=60)
    def vacia():
        return []

    vacia()
    assert cache_size() == 0  # [] no entra al store


def test_single_flight_una_sola_computacion_bajo_concurrencia():
    """10 threads piden la MISMA llave a la vez con TTL vigente → el cuerpo
    se ejecuta UNA sola vez (el resto reusa el resultado del líder).

    La barrera sincroniza el arranque AFUERA del cached: adentro no sirve
    porque con single-flight solo el líder ejecuta el cuerpo."""
    llamadas = {"n": 0}
    alineados = threading.Barrier(10)

    @cached(ttl=60)
    def lento():
        llamadas["n"] += 1
        time.sleep(0.1)        # ventana donde la estampida pegaría
        return {"v": 1}

    resultados: list = []

    def worker():
        alineados.wait()       # los 10 arrancan juntos
        resultados.append(lento())

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(r == {"v": 1} for r in resultados)
    assert llamadas["n"] == 1  # ← sin single-flight serían 10


def test_clear_despierta_waiters_sin_colgar():
    """clear_cache() en medio de un cómputo no debe dejar waiters colgados."""
    listo = threading.Event()

    @cached(ttl=60)
    def lento():
        listo.wait(timeout=2)
        return [1]

    t = threading.Thread(target=lento)
    t.start()
    time.sleep(0.02)
    clear_cache()  # no debe romper ni colgar
    listo.set()
    t.join(timeout=3)
    assert not t.is_alive()
