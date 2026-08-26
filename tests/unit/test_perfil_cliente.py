"""Tests de la FICHA OPERATIVA de un cliente (api/services/perfil_cliente_sql.py).

Lo que cuidan: que VOLUMEN y ARANCEL no se filtren igual. Confundirlos no rompe
nada — devuelve un número plausible y equivocado.
"""
from __future__ import annotations

from datetime import date

from api.services import perfil_cliente_sql as P
from api.services.comercial_sql import _PESIF, _arancel_where, _pesif


# ── Volumen y arancel: DOS reglas distintas ─────────────────────────────────
def test_el_volumen_excluye_los_cierres():
    """La apertura de la caución ya contó el volumen; sumar el cierre lo cuenta
    dos veces y el número sigue pareciendo razonable."""
    assert "es_cierre" in P._VOL_WHERE
    assert "false" in P._VOL_WHERE
    assert "solicitud" in P._VOL_WHERE


def test_el_arancel_INCLUYE_los_cierres():
    """El arancel de una caución vive SOLO en el cierre: filtrarlo lo haría
    desaparecer entero sin que nada avise."""
    assert "es_cierre" not in _arancel_where("o")


def test_las_dos_reglas_no_son_la_misma():
    assert _arancel_where("o") != P._VOL_WHERE


# ── Pesificación: una sola definición ───────────────────────────────────────
def test_el_bruto_se_pesifica_al_mep_DEL_BOLETO():
    """Sumar pesos con dólares da un número que parece plata y no lo es. Y tiene
    que ser el mep del boleto, no el de hoy: si no, el share de un mes viejo se
    mueve con el dólar de esta mañana."""
    e = _pesif("o", "bruto")
    assert "o.moneda = 'ARS'" in e
    assert "o.bruto" in e and "o.mep" in e


def test_la_pesificacion_de_negocio_movimientos_no_cambio():
    """`_PESIF` lo comparten media docena de queries del Tablero Comercial: el
    refactor a función tiene que dar EXACTAMENTE el mismo SQL."""
    assert _PESIF == ("CASE WHEN moneda = 'ARS' THEN abs(COALESCE(importe, 0)) "
                      "ELSE abs(COALESCE(importe, 0)) * COALESCE(mep, 0) END")
    assert _pesif() == _PESIF


# ── La ventana de meses ─────────────────────────────────────────────────────
def test_la_ventana_incluye_el_mes_de_hasta():
    ini, meses = P._ventana(3, date(2026, 8, 31))
    assert ini == date(2026, 6, 1)
    assert [m for m, _, _ in meses] == ["2026-06", "2026-07", "2026-08"]
    assert [lab for _, lab, _ in meses] == ["jun-26", "jul-26", "ago-26"]


def test_la_ventana_cruza_el_año():
    ini, meses = P._ventana(3, date(2026, 1, 31))
    assert ini == date(2025, 11, 1)
    assert [lab for _, lab, _ in meses] == ["nov-25", "dic-25", "ene-26"]


def test_todos_los_meses_estan_en_la_lista():
    """La lista de meses se arma en Python y no sale de la query: un mes sin
    operaciones tiene que dibujar una barra en CERO. Un mes ausente y un mes en
    cero no se ven igual en un gráfico."""
    _, meses = P._ventana(12, date(2026, 8, 31))
    assert len(meses) == 12
    assert meses[0][1] == "sep-25" and meses[-1][1] == "ago-26"


def test_una_ventana_absurda_no_dispara_un_scan_de_toda_la_historia():
    # 0 / None son "no me dijeron nada" → default; un número enorme se capea.
    assert len(P._ventana(0, date(2026, 8, 31))[1]) == P.MESES_DEF
    assert len(P._ventana(None, date(2026, 8, 31))[1]) == P.MESES_DEF
    assert len(P._ventana(9999, date(2026, 8, 31))[1]) == P.MESES_MAX
    assert len(P._ventana(-5, date(2026, 8, 31))[1]) == 1
    assert 1 <= P.MESES_DEF <= P.MESES_MAX


def test_cada_mes_trae_su_fin_de_mes():
    _, meses = P._ventana(2, date(2026, 3, 31))
    assert [f for _, _, f in meses] == [date(2026, 2, 28), date(2026, 3, 31)]
