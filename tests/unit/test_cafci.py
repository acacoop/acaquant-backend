"""Tests de core.cafci — parseo del patrón FCI de la `unidad`.

Fija el contrato del que dependen: el writer diario (auto-alta de assets FCI con
ticker), el backfill scripts.backfill_fci_ticker y el control fci_incompletos.
El `ticker` = nombre del fondo = TODO lo que va después del código CAFCI.
"""
from __future__ import annotations

import pytest

from core.cafci import es_fci_unidad, extract_cafci, nombre_fci


@pytest.mark.parametrize("unidad, esperado", [
    ("[1047] CAFCI518-1047 - Argenfunds Ahorro Pesos - Clase B",
     "Argenfunds Ahorro Pesos - Clase B"),
    # Conserva los guiones internos (la clase) y el prefijo "FCI" cuando está.
    ("[4111] CAFCI1341-4111 - FCI ACA Valores Retorno Total - Clase C",
     "FCI ACA Valores Retorno Total - Clase C"),
    ("[14937] CAFCI560-1133 - Toronto Trust Ahorro - Clase B",
     "Toronto Trust Ahorro - Clase B"),
    ("[1695] CAFCI1695-378 - FCI Megaqm Liquidez Dolar - Clase A",
     "FCI Megaqm Liquidez Dolar - Clase A"),
])
def test_nombre_fci_extrae_el_nombre_despues_del_codigo(unidad, esperado):
    assert nombre_fci(unidad) == esperado


@pytest.mark.parametrize("unidad", [
    None, "", "ARS",
    "[100] MERV - XMEV - AL30 - CI",   # bono: NO es FCI aunque tenga " - "
])
def test_nombre_fci_none_si_no_es_fci(unidad):
    assert nombre_fci(unidad) is None
    assert es_fci_unidad(unidad) is False


def test_es_fci_y_extract_cafci_consistentes():
    u = "[1047] CAFCI518-1047 - Argenfunds Ahorro Pesos - Clase B"
    assert es_fci_unidad(u) is True
    assert extract_cafci(u) == "CAFCI518-1047"
