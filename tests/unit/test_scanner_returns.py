"""Tests de scanner_sql.get_ticker_returns — la serie de retornos diarios.

Alimenta dos cosas del Scanner: el histograma (`returns`) y el gráfico de
retornos en el tiempo (`serie`, con fecha). Lo que congelan:

- que cada retorno quede pegado a SU fecha (el bug que había con los huecos);
- que el primer día no invente un retorno;
- que `serie` y `returns` sean el mismo dato, en distinta unidad.

SQL mockeado: no toca la DB.
"""
from __future__ import annotations

from datetime import date

import pytest

from api.services import scanner_sql


@pytest.fixture
def precios(monkeypatch):
    """Inyecta la serie cruda que devolvería la query y saltea el cache."""
    filas: list[dict] = []

    class _Cur:
        def execute(self, *a, **kw):
            return None

        def fetchall(self):
            return filas

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self, **kw):
            return _Cur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Pool:
        def connection(self):
            return _Conn()

    monkeypatch.setattr(scanner_sql, "get_pool", lambda: _Pool())
    monkeypatch.setattr(scanner_sql, "_resolve_underlying", lambda t: t.upper())
    return filas


def _sin_cache(fn):
    """get_ticker_returns está @cached: se invoca la función real de adentro."""
    return getattr(fn, "__wrapped__", fn)


def test_cada_retorno_queda_pegado_a_su_fecha(precios):
    precios += [
        {"fecha": date(2026, 7, 1), "close": 100.0},
        {"fecha": date(2026, 7, 2), "close": 110.0},   # +10% el 2
        {"fecha": date(2026, 7, 3), "close": 99.0},    # -10% el 3
    ]
    r = _sin_cache(scanner_sql.get_ticker_returns)(ticker="nvda")
    assert r["serie"] == [
        {"fecha": "2026-07-02", "ret_pct": 10.0},
        {"fecha": "2026-07-03", "ret_pct": -10.0},
    ]
    assert r["last_fecha"] == "2026-07-03"


def test_un_hueco_no_corre_las_fechas(precios):
    """REGRESIÓN: los cierres nulos se filtraban del lado de los precios pero
    no de las fechas, así que un solo día sin dato desalineaba TODA la serie
    posterior — cada retorno terminaba mostrándose en el día equivocado."""
    precios += [
        {"fecha": date(2026, 7, 1), "close": 100.0},
        {"fecha": date(2026, 7, 2), "close": None},    # feriado / sin dato
        {"fecha": date(2026, 7, 3), "close": 120.0},   # +20% contra el 1
    ]
    r = _sin_cache(scanner_sql.get_ticker_returns)(ticker="nvda")
    assert r["serie"] == [{"fecha": "2026-07-03", "ret_pct": 20.0}]


def test_el_primer_dia_no_tiene_retorno(precios):
    """No hay contra qué compararlo: emitirlo como 0% sería inventar una rueda
    plana que nunca existió."""
    precios += [{"fecha": date(2026, 7, 1), "close": 100.0}]
    r = _sin_cache(scanner_sql.get_ticker_returns)(ticker="nvda")
    assert r["serie"] == [] and r["returns"] == []


def test_serie_y_returns_son_el_mismo_dato(precios):
    """El histograma y el gráfico tienen que contar lo mismo: `ret_pct` es el
    retorno × 100, no un cálculo aparte que pueda divergir."""
    precios += [
        {"fecha": date(2026, 7, 1), "close": 100.0},
        {"fecha": date(2026, 7, 2), "close": 101.5},
        {"fecha": date(2026, 7, 3), "close": 99.0},
    ]
    r = _sin_cache(scanner_sql.get_ticker_returns)(ticker="nvda")
    assert len(r["serie"]) == len(r["returns"])
    for punto, ret in zip(r["serie"], r["returns"], strict=True):
        assert punto["ret_pct"] == pytest.approx(ret * 100, abs=1e-4)


def test_sin_precios_devuelve_vacio(precios):
    r = _sin_cache(scanner_sql.get_ticker_returns)(ticker="nada")
    assert r["serie"] == [] and r["returns"] == [] and r["last_fecha"] is None


def test_todos_los_cierres_nulos_no_revienta(precios):
    precios += [{"fecha": date(2026, 7, 1), "close": None}]
    r = _sin_cache(scanner_sql.get_ticker_returns)(ticker="nvda")
    assert r["serie"] == [] and r["last_fecha"] is None
