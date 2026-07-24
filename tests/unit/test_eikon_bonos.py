"""Tests de core.eikon_bonos — soberanos offshore (watchlist "GD30 OFF").

Fija el mapeo RIC→bono provisto por el user (MarketAxess, 2026-07-24) y la
cadena de fallback del precio (las páginas "=1M" no publican siempre CF_LAST).
"""
from __future__ import annotations

from core.eikon_bonos import BONOS_OFF, precio_off, universo_bonos_off


def test_mapeo_completo_y_sin_duplicados():
    assert len(BONOS_OFF) == 11
    assert BONOS_OFF["040114HS2=1M"] == "GD30"
    assert BONOS_OFF["ARARGE3209S=1M"] == "AL30"
    assert BONOS_OFF["ARARGE3209U=1M"] == "AE38"
    # los tickers locales no se repiten
    bonos = list(BONOS_OFF.values())
    assert len(bonos) == len(set(bonos))
    unis = universo_bonos_off()
    assert {u["ric"] for u in unis} == set(BONOS_OFF)


def test_precio_off_fallback():
    assert precio_off({"last": 64.5}) == 64.5
    assert precio_off({"last": None, "primact": 63.2}) == 63.2
    assert precio_off({"last": None, "primact": None, "bid": 64.0, "ask": 65.0}) == 64.5
    assert precio_off({"bid": 64.0}) == 64.0        # solo una punta
    assert precio_off({}) is None                    # sin nada → celda vacía
    assert precio_off({"last": "basura"}) is None    # dato sucio no rompe
