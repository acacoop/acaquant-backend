"""Test del routing opt-in al carril de jobs (core.postgres.use_job_pool)."""
from __future__ import annotations

from core.postgres import _prefer_job_pool, use_job_pool


def test_use_job_pool_enruta_solo_dentro_del_bloque():
    assert _prefer_job_pool.get() is False          # default: carril web
    with use_job_pool():
        assert _prefer_job_pool.get() is True        # adentro: carril jobs
    assert _prefer_job_pool.get() is False          # afuera: vuelve a web


def test_use_job_pool_se_resetea_aunque_falle():
    try:
        with use_job_pool():
            assert _prefer_job_pool.get() is True
            raise ValueError("boom")
    except ValueError:
        pass
    assert _prefer_job_pool.get() is False          # se restauró pese a la excepción
