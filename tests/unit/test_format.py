"""Tests de dashboard/shared/format.py — helpers de formateo."""
import sys
from unittest.mock import MagicMock

# Stub de streamlit: format.py hace `import streamlit as st` al top level.
# No necesitamos Streamlit real para testear las funciones puras.
sys.modules.setdefault("streamlit", MagicMock())

from dashboard.shared.format import (  # noqa: E402
    df_height,
    fmt_money,
    fmt_nom,
    fmt_vol,
    short_name,
)


# ─── fmt_money ──────────────────────────────────────────────────────────

def test_fmt_money_billones():
    assert fmt_money(2_500_000_000) == "$2.5B"


def test_fmt_money_millones():
    assert fmt_money(3_400_000) == "$3.4M"


def test_fmt_money_miles():
    assert fmt_money(12_345) == "$12K"


def test_fmt_money_chico():
    assert fmt_money(500) == "$500"


def test_fmt_money_none_devuelve_cero():
    assert fmt_money(None) == "$0"


def test_fmt_money_cero():
    assert fmt_money(0) == "$0"


# ─── fmt_nom (igual lógica sin signo $) ────────────────────────────────

def test_fmt_nom_billones_con_2_decimales():
    assert fmt_nom(2_500_000_000) == "2.50B"


def test_fmt_nom_millones_con_1_decimal():
    assert fmt_nom(3_400_000) == "3.4M"


def test_fmt_nom_none():
    assert fmt_nom(None) == "0"


# ─── fmt_vol ────────────────────────────────────────────────────────────

def test_fmt_vol_cero_devuelve_guion():
    assert fmt_vol(0) == "-"


def test_fmt_vol_none_devuelve_guion():
    assert fmt_vol(None) == "-"


def test_fmt_vol_millones():
    assert fmt_vol(2_500_000) == "2.5M"


def test_fmt_vol_miles_con_k_minuscula():
    assert fmt_vol(12_345) == "12k"


def test_fmt_vol_chico():
    assert fmt_vol(500) == "500"


# ─── short_name ────────────────────────────────────────────────────────

def test_short_name_formato_con_3_partes():
    assert short_name("FOO - BAR - GGAL") == "GGAL"


def test_short_name_formato_con_mas_de_3_partes():
    assert short_name("A - B - C - D") == "C"


def test_short_name_sin_separador_devuelve_el_mismo():
    assert short_name("AL30D") == "AL30D"


def test_short_name_con_2_partes_devuelve_el_mismo():
    """Menos de 3 partes → sin split válido → devuelve input."""
    assert short_name("A - B") == "A - B"


# ─── df_height ──────────────────────────────────────────────────────────

def test_df_height_una_fila():
    assert df_height(1) == 38 + 35


def test_df_height_clamp_en_max():
    assert df_height(1000, max_h=500) == 500


def test_df_height_vacio():
    assert df_height(0) == 38
