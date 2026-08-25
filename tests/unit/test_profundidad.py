"""Tests de PROFUNDIDAD DE CLIENTES (api/services/profundidad_sql.py).

Congela lo que la tabla NO puede dejar de cumplir. Todo lo de acá es lógica pura
(meses, labels, predicados como string): no toca la base.
"""
from __future__ import annotations

from datetime import date

from api.services import profundidad_sql as P
from api.services.comercial_sql import _ULT_OP_WHERE, _act_where, _arancel_where


# ── El predicado de ACTIVIDAD es UNO solo ────────────────────────────────────
def test_activo_usa_el_mismo_predicado_que_estado_comercial():
    """PROFUNDIDAD y ESTADO COMERCIAL tienen que llamar "operación" a lo mismo.
    Si esto se rompe, las dos tabs pueden decir cosas distintas de la misma cuenta
    y ninguna de las dos falla."""
    assert _act_where() == _ULT_OP_WHERE
    assert _act_where("o") == "o.anulado_en IS NULL"


def test_arancel_no_excluye_los_cierres():
    """El arancel de caución vive SOLO en el boleto de cierre: filtrarlos (como sí
    hace el VOLUMEN) haría desaparecer aranceles reales sin que nada avise."""
    w = _arancel_where()
    assert "arancel > 0" in w
    assert "etapa IS DISTINCT FROM 'solicitud'" in w
    assert "es_cierre" not in w
    assert _arancel_where("o") == "o.arancel > 0 AND o.etapa IS DISTINCT FROM 'solicitud'"


# ── Meses: el eje de la tabla ────────────────────────────────────────────────
def test_label_es_mmm_aa_en_castellano():
    assert P._label(2025, 7) == "jul-25"
    assert P._label(2025, 12) == "dic-25"
    assert P._label(2026, 1) == "ene-26"


def test_arranca_en_el_inicio_del_ejercicio():
    ms = P._meses(None, None)
    assert ms[0]["mes"] == P.PROFUNDIDAD_INICIO
    assert ms[0]["label"] == "jul-25"


def test_meses_ascendentes_y_sin_huecos():
    ms = P._meses("2025-07", "2026-02")
    assert [m["mes"] for m in ms] == [
        "2025-07", "2025-08", "2025-09", "2025-10",
        "2025-11", "2025-12", "2026-01", "2026-02"]


def test_todo_se_mide_al_ultimo_dia_del_mes():
    """La regla central del pedido: 31/07, no 01/07. Y febrero bisiesto incluido."""
    ms = {m["mes"]: m for m in P._meses("2024-01", "2024-03")}
    assert ms["2024-01"]["ini"] == date(2024, 1, 1)
    assert ms["2024-01"]["fin"] == date(2024, 1, 31)
    assert ms["2024-02"]["fin"] == date(2024, 2, 29)   # bisiesto
    ms2 = {m["mes"]: m for m in P._meses("2025-02", "2025-02")}
    assert ms2["2025-02"]["fin"] == date(2025, 2, 28)


def test_nunca_dibuja_meses_futuros():
    """Una fila de un mes que todavía no pasó sería un cero que parece un dato."""
    hoy = P._hoy_art()
    ms = P._meses("2025-07", "2099-12")
    assert ms[-1]["mes"] == f"{hoy.year:04d}-{hoy.month:02d}"
    assert ms[-1]["en_curso"] is True
    assert all(m["en_curso"] is False for m in ms[:-1])


def test_mes_invalido_cae_al_default_sin_reventar():
    assert P._meses("chirimbolo", "2026-01")[0]["mes"] == P.PROFUNDIDAD_INICIO
    assert P._parse_mes("2025-99", "2025-07") == (2025, 7)
    assert P._parse_mes(None, "2025-07") == (2025, 7)


def test_hay_tope_de_meses():
    """Un `desde` mal tipeado no puede disparar un scan de toda la historia."""
    assert len(P._meses("1900-01", None)) <= P.MAX_MESES


def test_desde_posterior_al_hasta_no_devuelve_vacio():
    ms = P._meses("2026-01", "2025-07")
    assert len(ms) == 1 and ms[0]["mes"] == "2025-07"


# ── Una columna nueva no puede quedar a medio conectar ───────────────────────
def test_toda_metrica_sabe_auditarse():
    """Cada columna de la tabla declara QUÉ cuentas listar y CÓMO titularse. Sin esto,
    agregar una columna dejaría una celda que se puede clickear y no abre nada."""
    assert set(P.METRICAS) == set(P._FILTRO_METRICA)
    assert set(P.METRICAS) == set(P._TITULO_METRICA)


def test_los_filtros_de_metrica_particionan_el_universo():
    """`con_aum` + `sin_aum` tienen que dar el universo completo — si los dos
    predicados no son complementarios, las dos columnas dejan de sumar `clientes`."""
    con = P._FILTRO_METRICA["con_aum"][0]
    sin = P._FILTRO_METRICA["sin_aum"][0]
    for aum in (-10.0, 0.0, 0.01, 1_000.0, None):
        fila = {"aum": aum, "arancel": 0.0, "n_boletos": 0}
        assert con(fila) != sin(fila), aum
