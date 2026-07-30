"""Tests del cálculo puro de rango (quant.rango): ATR y Efficiency Ratio."""
from __future__ import annotations

import pytest

from quant.rango import (
    atr,
    efficiency_ratio,
    efficiency_ratio_ventanas,
    true_range,
)


def test_true_range_toma_el_gap_contra_el_cierre_previo():
    # rango del día = 5, pero gapeó desde 120 → el TR real es 110-95... no:
    # high=110, low=105, close_prev=95 → max(5, |110-95|=15, |105-95|=10) = 15
    assert true_range(110, 105, 95) == 15
    # sin gap: el rango del día manda
    assert true_range(110, 100, 105) == 10


def test_atr_none_sin_historia_suficiente():
    # 20 ruedas → solo 19 true ranges → insuficiente para ATR-20
    highs = [10.0] * 20
    lows = [9.0] * 20
    closes = [9.5] * 20
    assert atr(highs, lows, closes, periodo=20) is None


def test_atr_promedia_los_ultimos_true_ranges():
    # 21 cierres sin gaps, rango constante de 2 → ATR-20 = 2
    highs = [11.0] * 21
    lows = [9.0] * 21
    closes = [10.0] * 21
    assert atr(highs, lows, closes, periodo=20) == pytest.approx(2.0)


def test_atr_periodo_corto():
    # rango diario 1, sin gaps, 3 ruedas → ATR-2 = 1
    assert atr([2, 2, 2], [1, 1, 1], [1.5, 1.5, 1.5], periodo=2) == pytest.approx(1.0)


def test_efficiency_ratio_tendencia_limpia_es_1():
    # monótona creciente → dirección == suma de |cambios| → ER = 1
    assert efficiency_ratio([1, 2, 3, 4, 5]) == pytest.approx(1.0)


def test_efficiency_ratio_serrucho_tiende_a_0():
    # ida y vuelta al mismo punto → dirección neta 0 → ER = 0 (máximo choppy)
    assert efficiency_ratio([1, 2, 1, 2, 1]) == pytest.approx(0.0)
    # choppy con leve deriva → ER bajo (<0.5)
    er = efficiency_ratio([10, 12, 9, 11, 10.5])
    assert er is not None and er < 0.5


def test_efficiency_ratio_bordes():
    assert efficiency_ratio([]) is None
    assert efficiency_ratio([5]) is None
    assert efficiency_ratio([5, 5, 5]) is None  # sin movimiento


def test_efficiency_ratio_ventanas_recorta_reciente():
    closes = list(range(1, 41))  # 40 barras monótonas
    r = efficiency_ratio_ventanas(closes, ventana_reciente=30)
    assert r["er_dia"] == pytest.approx(1.0)
    assert r["er_reciente"] == pytest.approx(1.0)  # las últimas 30 también monótonas
