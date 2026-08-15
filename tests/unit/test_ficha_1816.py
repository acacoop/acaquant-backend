"""Estandarización del emisor contra 1816 (jobs/ficha_1816.py).

Lo que se congela son las dos reglas que evitan que un job que PISA haga daño:
no tocar lo que 1816 no sabe, y no reescribir lo que ya está bien.
"""
from __future__ import annotations

from jobs.ficha_1816 import decidir_emisores


def _f(ticker, nuestro, de_1816):
    return {"ticker": ticker, "nuestro": nuestro, "de_1816": de_1816}


def test_estandariza_la_forma_de_escribir():
    """El caso real: el mismo banco en dos filas distintas."""
    cambios = decidir_emisores([_f("AFCHO", "BCO.COMAFI", "Banco Comafi"),
                                _f("AFCIO", "B. Comafi", "Banco Comafi")])
    assert [c["emisor"] for c in cambios] == ["Banco Comafi", "Banco Comafi"]


def test_completa_el_que_estaba_vacio():
    cambios = decidir_emisores([_f("CP36O", "", "CGC")])
    assert cambios == [{"ticker": "CP36O", "emisor": "CGC", "antes": ""}]


def test_sin_dato_en_1816_no_se_toca():
    """No tener nombre canónico no habilita a borrar el que hay — y hay títulos
    que 1816 no lista."""
    assert decidir_emisores([_f("XXXO", "Emisor Nuestro", None)]) == []
    assert decidir_emisores([_f("XXXO", "Emisor Nuestro", "   ")]) == []


def test_lo_que_ya_coincide_no_se_reescribe():
    """Sin esto el job 'actualizaría' cientos de filas por día y el log dejaría
    de servir para ver qué cambió de verdad."""
    assert decidir_emisores([_f("YM34O", "YPF", "YPF")]) == []


def test_los_espacios_al_borde_no_cuentan_como_cambio():
    assert decidir_emisores([_f("YM34O", " YPF ", "YPF")]) == []


def test_vacio_de_los_dos_lados_no_genera_cambio():
    assert decidir_emisores([_f("XXXO", None, None)]) == []
