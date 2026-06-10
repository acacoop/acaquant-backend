"""Tests de quant/intraday.py — vueltas zigzag, momentum y posición en rango.

Series sintéticas con movimientos conocidos: la métrica 'vueltas' es el
corazón del TRADE LAB intradía, así que se fija el comportamiento exacto
(pata completada, pata en curso, ruido bajo el umbral).
"""
from __future__ import annotations

from quant.intraday import (
    analizar_vueltas,
    contar_vueltas,
    momentum_pct,
    momentum_por_tiempo,
    posicion_en_rango,
)


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


def test_pata_en_curso_subiendo():
    # Subió 1% y está en el máximo: pata viva long de ~1%.
    r = analizar_vueltas([100.0, 100.5, 101.0], 0.5)
    assert r["pata_dir"] == 1
    assert r["pata_pct"] == 1.0


def test_pata_en_curso_tras_reversion():
    # Subió a 101 y revirtió a 100.4: pata nueva SHORT desde el pivote 101.
    r = analizar_vueltas([100.0, 101.0, 100.4], 0.5)
    assert r["vueltas"] >= 1
    assert r["pata_dir"] == -1
    assert r["pata_pct"] < 0


def test_pata_sin_direccion_definida():
    r = analizar_vueltas([100.0, 100.1, 100.05], 0.5)
    assert r["pata_dir"] == 0
    assert r["pata_pct"] == 0.0


def test_momentum_por_tiempo_usa_reloj_no_barras():
    # Dos barras separadas por 60': ventana de 15' debe medir contra la barra
    # vieja (≤ corte), no devolver 0 por "faltan barras".
    pares = [
        ("2026-06-10T14:00:00Z", 100.0),
        ("2026-06-10T15:00:00Z", 101.0),
    ]
    assert momentum_por_tiempo(pares, 15) == 1.0
    # Barras dentro de la ventana: base = primer punto.
    pares2 = [
        ("2026-06-10T14:50:00Z", 100.0),
        ("2026-06-10T14:55:00Z", 100.5),
        ("2026-06-10T15:00:00Z", 101.0),
    ]
    assert momentum_por_tiempo(pares2, 15) == 1.0
    # Con historia suficiente toma el cierre ≤ último − 15'.
    pares3 = [
        ("2026-06-10T14:00:00Z", 90.0),
        ("2026-06-10T14:45:00Z", 100.0),
        ("2026-06-10T15:00:00Z", 101.0),
    ]
    assert momentum_por_tiempo(pares3, 15) == 1.0


def test_posicion_en_rango():
    assert posicion_en_rango(100.0, 100.0, 110.0) == 0.0
    assert posicion_en_rango(110.0, 100.0, 110.0) == 100.0
    assert posicion_en_rango(105.0, 100.0, 110.0) == 50.0
    assert posicion_en_rango(105.0, 100.0, 100.0) is None  # rango degenerado
    assert posicion_en_rango(None, 100.0, 110.0) is None  # type: ignore[arg-type]
