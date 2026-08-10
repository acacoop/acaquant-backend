"""Reglas de autocompletado de `portafolio.assets` (jobs/assets_autofill.py).

Lo que se congela acá es el contrato del motor: completa SOLO lo vacío, nunca
pisa lo cargado a mano, y la firma de FINANCIAMIENTO no puede tragarse una
unidad de renta variable / CEDEAR (el corchete con id de especie).
"""
from __future__ import annotations

from jobs.assets_autofill import (
    REGLAS,
    _regla_fci,
    _regla_financiamiento,
    planificar,
)

_UNIDAD_FIN = "[*BIN031000050] *BIN031000050 Nro. 29805263 Vto. 03/10/2026"
_UNIDAD_FCI = "[1047] CAFCI518-1047 - Argenfunds Ahorro Pesos - Clase B"


def test_financiamiento_deriva_cartera_ticker_y_vto():
    assert _regla_financiamiento({"unidad": _UNIDAD_FIN}) == {
        "cartera": "FINANCIAMIENTO",
        "ticker": "*BIN031000050",
        "vencimiento": "2026-10-03",   # dd/mm/aaaa → ISO
    }
    assert _regla_financiamiento({"unidad": "[#UD0281260001] #UD0281260001 "
                                            "Nro. 155077 Vto. 30/12/2026"})["ticker"] \
        == "#UD0281260001"


def test_financiamiento_no_matchea_el_resto_del_catalogo():
    # El corchete con id de especie NO repite el token siguiente.
    for u in ("[9131] YPFD - CEDEAR YPF",
              "[1047] CAFCI518-1047 - Argenfunds Ahorro Pesos",
              "ARS",
              "[*BIN031000050] *BIN031000050 Nro. 29805263",          # sin Vto.
              "[*BIN031000050] *BIN031000050 Nro. 29805263 Vto. 31/02/2026"):  # fecha falsa
        assert _regla_financiamiento({"unidad": u}) == {}


def test_fci_deriva_codigo_y_nombre():
    assert _regla_fci({"unidad": _UNIDAD_FCI}) == {
        "cartera": "FCI",
        "ticker": "Argenfunds Ahorro Pesos - Clase B",
        "cafci": "CAFCI518-1047",
    }


def test_planificar_completa_solo_lo_vacio():
    rows = [{"unidad": _UNIDAD_FIN, "cartera": None, "ticker": "",
             "vencimiento": "NO APLICA"}]
    cambios, _ = planificar(rows, REGLAS)
    assert cambios[_UNIDAD_FIN] == {"cartera": "FINANCIAMIENTO",
                                    "ticker": "*BIN031000050",
                                    "vencimiento": "2026-10-03"}


def test_planificar_no_pisa_lo_cargado_y_reporta_conflicto():
    rows = [{"unidad": _UNIDAD_FIN, "cartera": "HD",
             "ticker": "*BIN031000050", "vencimiento": None}]
    cambios, reporte = planificar(rows, REGLAS)
    # Solo se completa el vencimiento; la cartera cargada a mano queda intacta.
    assert cambios[_UNIDAD_FIN] == {"vencimiento": "2026-10-03"}
    conflictos = reporte["financiamiento"]["conflictos"]
    assert len(conflictos) == 1 and "cartera" in conflictos[0]
