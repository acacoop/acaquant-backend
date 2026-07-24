"""Tests de core.eikon_chicago — modelado de familias CBOT y conversión a USD/t.

Fija el contrato del tab CHICAGO: los RICs del universo, la posición que sale
del sufijo del RIC (no del índice — hay familias que saltean la c3) y que la
conversión con factor nunca rompe con datos faltantes.
"""
from __future__ import annotations

from core.eikon_chicago import (
    FAMILIAS,
    RIC_FAMILIA,
    _fila,
    _pos,
    universo_chicago,
)


def test_familias_integridad():
    """5 familias, RICs únicos, factores positivos (van directo a la valuación)."""
    assert list(FAMILIAS) == ["soja", "aceite_soja", "maiz", "trigo", "harina_soja"]
    todos = [r for cfg in FAMILIAS.values() for r in cfg["rics"]]
    assert len(todos) == len(set(todos)) == 25
    for cfg in FAMILIAS.values():
        assert cfg["factor"] > 0
        assert cfg["label"]


def test_ric_familia_y_universo_consistentes():
    assert RIC_FAMILIA["Sc1"] == "soja"
    assert RIC_FAMILIA["BOc6"] == "aceite_soja"
    assert RIC_FAMILIA["SMc4"] == "harina_soja"
    unis = universo_chicago()
    assert {u["ric"] for u in unis} == set(RIC_FAMILIA)
    assert all(u["familia"] in FAMILIAS for u in unis)


def test_posicion_sale_del_sufijo_no_del_indice():
    """Aceite y Harina saltean la c3 (así venía el script original del user):
    la posición debe salir del RIC, no del orden de la lista."""
    assert _pos("Sc1") == 1
    assert _pos("BOc4") == 4      # 3er elemento de la lista, posición 4
    assert _pos("SMc6") == 6
    assert FAMILIAS["aceite_soja"]["rics"][2] == "BOc4"


def test_fila_convierte_con_factor():
    f = _fila("Cc1", 0.393685, {"mes": "JUL6", "last": 400.0, "var_neta": -2.5}, None)
    assert f["mes"] == "JUL6"
    assert abs(f["precio"] - 400.0 * 0.393685) < 1e-9
    assert abs(f["variacion"] - (-2.5 * 0.393685)) < 1e-9
    assert f["posicion"] == 1


def test_fila_nunca_rompe_con_datos_faltantes():
    f = _fila("Wc2", 0.367444, {"mes": None, "last": None, "var_neta": "no-num"}, None)
    assert f["precio"] is None
    assert f["variacion"] is None
    assert f["ric"] == "Wc2"
