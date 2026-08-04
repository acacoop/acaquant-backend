"""Tasa ponderada por volumen en la vista MOVIMIENTOS (operaciones_sql).

La ponderación se calcula en SQL (server-side, para que el front no pondere). Se
valida la MATEMÁTICA con sqlite en memoria, replicando la expresión ARS real que
arma `_tasa_pond_expr`, más las dos propiedades que importan:
  - una fila SIN tasa no diluye el promedio (FILTER WHERE tasa IS NOT NULL);
  - un grupo sin ninguna tasa devuelve NULL (columna vacía), no 0 ni error.
"""
from __future__ import annotations

import sqlite3

import pytest

from api.services.operaciones_sql import _peso_bruto_row, _tasa_pond_expr


def _eval_ars(filas: list[tuple[float, float | None]]):
    """Corre la expresión ARS de tasa ponderada sobre `filas` (bruto, tasa)."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE operaciones (bruto REAL, tasa REAL)")
    db.executemany("INSERT INTO operaciones VALUES (?,?)", filas)
    return db.execute(
        f"SELECT {_tasa_pond_expr('ARS')} FROM operaciones"
    ).fetchone()[0]


def test_ponderada_por_bruto():
    # (100·6 + 300·6 + 100·-0,5) / 500 = 2350/500 = 4,7
    assert _eval_ars([(100, 6.0), (300, 6.0), (100, -0.5)]) == pytest.approx(4.7)


def test_fila_sin_tasa_no_diluye():
    """La fila (bruto 1000, tasa NULL) NO debe entrar al peso ni al numerador."""
    con = [(100, 6.0), (300, 6.0), (100, -0.5)]
    assert _eval_ars(con + [(1000, None)]) == pytest.approx(_eval_ars(con))


def test_grupo_sin_tasa_da_null():
    """Mercado ≠ MAV: todas las tasas NULL → columna vacía, no 0."""
    assert _eval_ars([(100, None), (200, None)]) is None


def test_una_sola_tasa_colapsa_a_ese_valor():
    """POR TÍTULO: dos boletos del mismo título a la misma tasa → esa tasa."""
    assert _eval_ars([(500, 7.0), (500, 7.0)]) == pytest.approx(7.0)


def test_tasa_negativa_se_pondera_con_signo():
    # (100·-0,5 + 300·-0,25) / 400 = (-50 -75)/400 = -0,3125
    assert _eval_ars([(100, -0.5), (300, -0.25)]) == pytest.approx(-0.3125)


def test_peso_dolarizado_difiere_del_nativo():
    """En USD_DOL el peso por fila convierte con mep; en ARS es bruto crudo."""
    assert _peso_bruto_row("ARS") == "COALESCE(bruto, 0)"
    assert "mep" in _peso_bruto_row("USD_DOL")


def test_expr_ignora_null_y_evita_div_cero():
    """Las dos piezas no negociables de la expresión."""
    expr = _tasa_pond_expr("ARS")
    assert "FILTER (WHERE tasa IS NOT NULL)" in expr
    assert "NULLIF(" in expr
