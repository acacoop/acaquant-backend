"""Reglas de autocompletado de `portafolio.assets` (jobs/assets_autofill.py).

Lo que se congela acá es el contrato del motor: completa SOLO lo vacío, nunca
pisa lo cargado a mano, y la firma de FINANCIAMIENTO no puede tragarse una
unidad de renta variable / CEDEAR (el corchete con id de especie).
"""
from __future__ import annotations

import pytest

from jobs.assets_autofill import (
    REGLAS,
    _regla_fci,
    _regla_financiamiento,
    _regla_ticker,
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


# ── Regla `ticker` (cualquier cartera) ───────────────────────────────────────

@pytest.mark.parametrize(("unidad", "esperado"), [
    # Sin descripción → el ticker es el id del corchete.
    ("[DLR012026]", "DLR012026"),
    ("[COMC17929J]", "COMC17929J"),
    ("[CRN.CME/AGO26]", "CRN.CME/AGO26"),
    # Con descripción y guion → hasta el primer guion.
    ("[43070] NZC6O - NZC6O - T.DEUDA BCO DE LA NACION ARG 6", "NZC6O"),
    ("[45698] TVPP - VALORES NEG VINC PBI $ 15/12/2035", "TVPP"),
    ("[56384] RB56O - ON ROMBO S. 56 $ VTO.19/08/25 C.G.", "RB56O"),
    ("[9131] YPFD - CEDEAR YPF", "YPFD"),
    # Con descripción y SIN guion → la descripción entera, sin romper.
    ("[43068] NZC4O", "NZC4O"),
    ("[620] INTRODUCT. ORD1V", "INTRODUCT. ORD1V"),
    ("[59573] ON PYME SION S.4 17/04/27 $", "ON PYME SION S.4 17/04/27 $"),
    ("[10390] Depósito U$S Ext", "Depósito U$S Ext"),
])
def test_ticker_desde_la_unidad(unidad, esperado):
    assert _regla_ticker({"unidad": unidad}) == {"ticker": esperado}


def test_ticker_no_toca_fci_ni_financiamiento():
    # FCI: el ticker es el nombre del fondo, no 'CAFCI518' (primer guion).
    assert _regla_ticker({"unidad": _UNIDAD_FCI}) == {}
    assert _regla_ticker({"unidad": "[1047] Fondo X", "cartera": "FCI"}) == {}
    # Financiamiento: ya lo resuelve su propia regla.
    assert _regla_ticker({"unidad": _UNIDAD_FIN}) == {}
    # Cash sin corchetes: no hay nada que derivar.
    assert _regla_ticker({"unidad": "ARS"}) == {}


def test_reglas_no_se_pisan_entre_si():
    rows = [{"unidad": _UNIDAD_FIN}, {"unidad": _UNIDAD_FCI},
            {"unidad": "[43070] NZC6O - NZC6O - T.DEUDA BCO DE LA NACION ARG 6"}]
    cambios, reporte = planificar(rows, REGLAS)
    assert not any(r["conflictos"] for r in reporte.values())
    assert cambios[_UNIDAD_FIN]["ticker"] == "*BIN031000050"
    assert cambios[_UNIDAD_FCI]["ticker"] == "Argenfunds Ahorro Pesos - Clase B"
