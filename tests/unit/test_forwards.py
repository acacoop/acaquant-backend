"""Tests de la matriz de tasas forward en engines/forwards.py."""

import pytest

from engines.forwards import calcular_matriz


def _instr(tc):
    return {"ticker": f"{tc}-24hs", "ticker_corto": tc}


def test_calcular_matriz_empty_si_menos_de_2_instrumentos():
    instr = [_instr("A")]
    tasas = {"A-24hs": {"TEA": 0.30, "duration": 0.5}}
    ordered, t, m = calcular_matriz(instr, tasas)
    assert ordered == []
    assert t == {}
    assert m == {}


def test_calcular_matriz_descarta_sin_TEA_o_duration():
    instr = [_instr("A"), _instr("B"), _instr("C")]
    tasas = {
        "A-24hs": {"TEA": 0.30, "duration": 0.5},
        "B-24hs": {"TEA": None, "duration": 1.0},
        "C-24hs": {"TEA": 0.40, "duration": 1.5},
    }
    ordered, _, _ = calcular_matriz(instr, tasas)
    assert "B" not in ordered
    assert ordered == ["A", "C"]


def test_forward_flat_curve_iguala_spot():
    # curva plana al 30% → cualquier forward debe dar ~30%
    instr = [_instr("A"), _instr("B"), _instr("C")]
    tasas = {
        "A-24hs": {"TEA": 0.30, "duration": 0.5},
        "B-24hs": {"TEA": 0.30, "duration": 1.0},
        "C-24hs": {"TEA": 0.30, "duration": 2.0},
    }
    _, _, m = calcular_matriz(instr, tasas)
    assert m["B"]["A"] == pytest.approx(0.30, abs=1e-5)
    assert m["C"]["A"] == pytest.approx(0.30, abs=1e-5)
    assert m["C"]["B"] == pytest.approx(0.30, abs=1e-5)


def test_forward_formula_conocida():
    # TEA_A=0.20 a 1y, TEA_B=0.25 a 2y
    # forward 1y→2y = (1.25² / 1.20) - 1 = 0.3021
    instr = [_instr("A"), _instr("B")]
    tasas = {
        "A-24hs": {"TEA": 0.20, "duration": 1.0},
        "B-24hs": {"TEA": 0.25, "duration": 2.0},
    }
    _, _, m = calcular_matriz(instr, tasas)
    esperado = (1.25**2 / 1.20) - 1
    assert m["B"]["A"] == pytest.approx(esperado, abs=1e-4)


def test_ordenamiento_por_duration_ascendente():
    instr = [_instr("C"), _instr("A"), _instr("B")]
    tasas = {
        "A-24hs": {"TEA": 0.30, "duration": 0.5},
        "B-24hs": {"TEA": 0.35, "duration": 1.0},
        "C-24hs": {"TEA": 0.40, "duration": 2.0},
    }
    ordered, _, _ = calcular_matriz(instr, tasas)
    assert ordered == ["A", "B", "C"]


def test_matrix_solo_corto_a_largo():
    instr = [_instr("A"), _instr("B")]
    tasas = {
        "A-24hs": {"TEA": 0.30, "duration": 0.5},
        "B-24hs": {"TEA": 0.35, "duration": 1.0},
    }
    _, _, m = calcular_matriz(instr, tasas)
    assert "B" in m
    assert "A" in m["B"]
    # no debe haber forward desde largo a corto
    assert "A" not in m or "B" not in m.get("A", {})
