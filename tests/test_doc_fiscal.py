"""Tests del matcher de documento fiscal (Control Automático).

La correctitud del cruce Excel(CUIT)↔cuentas(DNI/CUIT) es lo crítico de la
feature. Casos basados en la distribución real (scripts/diag_titular_cuit):
DNI 7-8, CUIT/CUIL/CDI 11.
"""
from __future__ import annotations

from core.doc_fiscal import claves_match, parse_titular, solo_digitos


def test_parse_titular():
    assert parse_titular("[DNI 93698623] MOLLO, NICOLAS") == ("DNI", "93698623")
    assert parse_titular("[CUIT 30604731018] ACME SA") == ("CUIT", "30604731018")
    assert parse_titular("[CUIL 27-12345678-9] X") == ("CUIL", "27123456789")
    assert parse_titular("sin bracket") == (None, None)
    assert parse_titular(None) == (None, None)


def test_solo_digitos():
    assert solo_digitos("20-23799949-3") == "20237999493"
    assert solo_digitos(" 6.608.151 ") == "6608151"
    assert solo_digitos(None) == ""


def test_cuit_excel_matchea_dni_nuestro():
    # Persona física: nosotros la tenemos como DNI 8; el Excel trae su CUIT.
    dni = "23799949"
    cuit_excel = "20237999493"          # 20-23799949-3
    assert claves_match(dni) & claves_match(cuit_excel), "el CUIT debe cruzar con el DNI embebido"


def test_cuit_excel_matchea_dni_7_digitos():
    # DNI de 7 dígitos (se zero-padea a 8 en el CUIT).
    dni = "6608151"
    cuit_excel = "20066081519"          # 20-06608151-9
    assert claves_match(dni) & claves_match(cuit_excel)


def test_cuit_matchea_cuit():
    # Persona jurídica: nosotros la tenemos como CUIT 11; el Excel trae el mismo.
    assert claves_match("30604731018") & claves_match("30604731018")


def test_no_cruza_distintos():
    assert not (claves_match("23799949") & claves_match("20111111112"))


def test_vacio_no_cruza():
    assert claves_match("") == set()
    assert not (claves_match("") & claves_match("20237999493"))
