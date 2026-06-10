"""Tests de quant/intraday.py — vueltas zigzag, momentum y posición en rango.

Series sintéticas con movimientos conocidos: la métrica 'vueltas' es el
corazón del TRADE LAB intradía, así que se fija el comportamiento exacto
(pata completada, pata en curso, ruido bajo el umbral).
"""
from __future__ import annotations

from quant.intraday import contar_vueltas, momentum_pct, posicion_en_rango


def test_vueltas_serie_corta_o_umbral_invalido():
    assert contar_vueltas([], 0.5) == (0, 0.0)
    assert contar_vueltas([100.0], 0.5) == (0, 0.0)
    assert contar_vueltas([100.0, 101.0], 0) == (0, 0.0)


def test_vueltas_ruido_bajo_el_umbral_no_cuenta():
    # Oscila ±0.2% — con umbral 0.5% no hay ninguna vuelta.
    serie = [100.0, 100.2, 99.9, 100.1, 99.95, 100.15]
    assert contar_vueltas(serie, 0.5) == (0, 0.0)


def test_vueltas_subida_simple_en_curso_cuenta():
    # Sube 1% sin revertir: una pata viva ≥ umbral → cuenta 1.
    serie = [100.0, 100.4, 100.7, 101.0]
    n, mejor = contar_vueltas(serie, 0.5)
    assert n == 1
    assert mejor == 1.0


def test_vueltas_ida_y_vuelta_cuenta_dos():
    # Sube 1% (100→101), baja 1% (101→99.99): dos patas ≥ 0.5%.
    serie = [100.0, 100.5, 101.0, 100.5, 99.99]
    n, mejor = contar_vueltas(serie, 0.5)
    assert n == 2
    assert mejor == 1.0


def test_vueltas_tres_patas_zigzag():
    # 100 → 101 (+1%) → 100 (−0.99%) → 101 (+1%): tres patas con umbral 0.5.
    serie = [100.0, 101.0, 100.0, 101.0]
    n, _ = contar_vueltas(serie, 0.5)
    assert n == 3


def test_vueltas_ignora_nones_y_ceros():
    serie = [100.0, None, 0, 100.5, 101.0]
    n, mejor = contar_vueltas(serie, 0.5)  # type: ignore[arg-type]
    assert n == 1
    assert mejor == 1.0


def test_momentum_basico_y_ventana_corta():
    # 16 barras: la base de 15' es la primera.
    serie = [100.0] * 1 + [100.0 + i * 0.1 for i in range(1, 16)]
    m = momentum_pct(serie, 15)
    assert m is not None and m > 0
    # Serie más corta que la ventana: usa el primer punto.
    assert momentum_pct([100.0, 101.0], 15) == 1.0
    assert momentum_pct([100.0], 15) is None


def test_posicion_en_rango():
    assert posicion_en_rango(100.0, 100.0, 110.0) == 0.0
    assert posicion_en_rango(110.0, 100.0, 110.0) == 100.0
    assert posicion_en_rango(105.0, 100.0, 110.0) == 50.0
    assert posicion_en_rango(105.0, 100.0, 100.0) is None  # rango degenerado
    assert posicion_en_rango(None, 100.0, 110.0) is None  # type: ignore[arg-type]
