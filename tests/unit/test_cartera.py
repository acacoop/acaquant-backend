"""`core/cartera.py` — PURO: sin base, sin red. Doc: `docs/AGENT.md` §0.ej."""
from __future__ import annotations

from core import cartera as ct


def test_dolar_linked_gana_sobre_la_moneda():
    # dolar_linked manda incluso si la moneda de denominación es USD/EUR o ARS.
    assert ct.de_ejes("USD", "dolar_linked") == ct.DL
    assert ct.de_ejes("EUR", "dolar_linked") == ct.DL
    assert ct.de_ejes("ARS", "dolar_linked") == ct.DL
    # Mayúsculas/espacios no rompen la comparación.
    assert ct.de_ejes("usd", "  DOLAR_LINKED  ") == ct.DL


def test_usd_y_eur_son_hd():
    assert ct.de_ejes("USD", "fija") == ct.HD
    assert ct.de_ejes("EUR", "cer") == ct.HD
    # upper/strip: minúsculas y espacios no rompen la clasificación.
    assert ct.de_ejes("  usd  ", "fija") == ct.HD


def test_ars_es_ars():
    assert ct.de_ejes("ARS", "cer") == ct.ARS
    assert ct.de_ejes("ARS", "tamar") == ct.ARS
    assert ct.de_ejes("ars", "fija") == ct.ARS


def test_vacio_u_otra_moneda_no_propone():
    assert ct.de_ejes("", "") == ""
    assert ct.de_ejes("GBP", "fija") == ""
    assert ct.de_ejes(None, None) == ""
