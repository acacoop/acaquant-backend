"""TXT del asiento de ajuste (Tesorería → BANCO A BANCO).

El formato lo consume HYGIRUS, así que el contrato es literal: cabecera fija,
TAB como separador, coma decimal y el N° HYGIRUS del banco (no su nombre).
"""
from datetime import datetime
from decimal import Decimal

from api.services.tesoreria import _importe_hygirus, armar_txt_bb

AHORA = datetime(2025, 3, 10, 13, 3, 15)
HYG = {("BANCO A", "ARS"): "1101", ("BANCO B", "ARS"): "1102"}


def test_importe_usa_coma_y_no_separa_miles():
    assert _importe_hygirus(Decimal("6000000000")) == "6000000000"
    assert _importe_hygirus(Decimal("227256167.70")) == "227256167,7"
    assert _importe_hygirus(Decimal("1234.56")) == "1234,56"


def test_txt_arma_cabecera_y_dos_lineas_por_fila():
    filas = [{"cta_debito": "BANCO A", "cta_credito": "BANCO B",
              "unidad": "ARS", "importe": Decimal("2000000000")}]
    txt, faltantes = armar_txt_bb(filas, HYG, AHORA)
    assert not faltantes
    assert txt.splitlines() == [
        "10/03/2025 13:03:15 Asiento de ajuste",
        "-2000000000\t1101\tARS",
        "2000000000\t1102\tARS",
    ]


def test_cuenta_sin_hygirus_se_reporta_como_faltante():
    filas = [{"cta_debito": "BANCO A", "cta_credito": "BANCO SIN",
              "unidad": "ARS", "importe": Decimal("10")}]
    _, faltantes = armar_txt_bb(filas, HYG, AHORA)
    assert faltantes == ["BANCO SIN (ARS)"]


def test_guion_largo_cuenta_como_sin_hygirus():
    filas = [{"cta_debito": "BANCO A", "cta_credito": "BANCO B",
              "unidad": "ARS", "importe": Decimal("10")}]
    _, faltantes = armar_txt_bb(filas, {**HYG, ("BANCO B", "ARS"): "—"}, AHORA)
    assert faltantes == ["BANCO B (ARS)"]
