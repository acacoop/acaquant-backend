"""Tests de engines/breakevens.py — cálculo de breakeven CER/Lecap."""

from datetime import date

import pytest

from engines.breakevens import calcular_breakevens


def _par(lecap="S31E6", cer="TX26", vto="2026-12-31"):
    return {
        "lecap_corto": lecap,
        "cer_corto": cer,
        "lecap_ticker": f"{lecap}-24hs",
        "cer_ticker": f"{cer}-24hs",
        "fecha_vencimiento": vto,
    }


def test_descarta_pares_con_menos_de_30_dias():
    pares = [_par(vto="2026-04-20")]  # ~5 días desde ref
    fecha_ref = date(2026, 4, 15)
    r = calcular_breakevens(pares, {}, {}, {}, fecha_ref)
    assert r == []


def test_breakeven_formula_paridad_100_igual_a_TEM():
    # Si paridad = 100%, el CER no genera retorno extra,
    # entonces el breakeven mensual debería ser igual al TEM del Lecap.
    pares = [_par(vto="2027-04-15")]
    tems = {"S31E6-24hs": 0.03}
    paridades = {"TX26-24hs": 100.0}
    fecha_ref = date(2026, 4, 15)

    r = calcular_breakevens(pares, tems, paridades, {}, fecha_ref)
    assert len(r) == 1
    entry = r[0]
    assert "breakeven_mensual" in entry
    assert entry["breakeven_mensual"] == pytest.approx(0.03, abs=1e-3)


def test_breakeven_calculo_manual():
    # 180 días al vto, TEM Lecap=4%, paridad CER=90
    # retorno    = 1.04^(180/30) - 1 = 1.04^6 - 1 = 0.26532
    # inflacion  = 1.26532 * 0.90 - 1 = 0.13879
    # breakeven  = 1.13879^(30/180) - 1 = 1.13879^(1/6) - 1 ≈ 0.02185
    pares = [_par(vto="2026-10-12")]  # ~180 días desde 2026-04-15
    fecha_ref = date(2026, 4, 15)
    dias = (date(2026, 10, 12) - fecha_ref).days
    tem = 0.04
    paridad = 90.0

    tems = {"S31E6-24hs": tem}
    paridades = {"TX26-24hs": paridad}

    r = calcular_breakevens(pares, tems, paridades, {}, fecha_ref)
    entry = r[0]

    retorno_esp = (1 + tem) ** (dias / 30) - 1
    inflacion_esp = (1 + retorno_esp) * (paridad / 100) - 1
    be_esp = (1 + inflacion_esp) ** (30 / dias) - 1

    assert entry["retorno_acumulado"] == pytest.approx(retorno_esp, abs=1e-4)
    assert entry["inflacion_acumulada"] == pytest.approx(inflacion_esp, abs=1e-4)
    assert entry["breakeven_mensual"] == pytest.approx(be_esp, abs=1e-4)


def test_sin_tem_o_paridad_no_calcula_breakeven():
    pares = [_par(vto="2026-12-31")]
    r = calcular_breakevens(pares, {}, {}, {}, date(2026, 4, 15))
    assert len(r) == 1
    assert "breakeven_mensual" not in r[0]


def test_numeracion_incremental():
    pares = [
        _par(lecap="L1", cer="C1", vto="2026-12-31"),
        _par(lecap="L2", cer="C2", vto="2027-06-30"),
    ]
    r = calcular_breakevens(pares, {}, {}, {}, date(2026, 4, 15))
    assert [e["n"] for e in r] == [1, 2]
