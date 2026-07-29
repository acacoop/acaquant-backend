"""Tests unit del motor puro ESTRATEGIA QUANT (quant/estrategia.py).

Casos fijos, sin DB — validan la SEMÁNTICA de cada factor (el signo y la
saturación son el contrato con el trader). Doc: docs/ESTRATEGIA_QUANT.md.
"""
from __future__ import annotations

from quant.estrategia import (
    PESOS_V1,
    factor_alineacion,
    factor_confluencia,
    factor_nafta_papel,
    factor_recorrido_indice,
    score_estrategia,
    zonas_confluencia,
)

PIVOTS = {"pp": 100.0, "r1": 102.0, "r2": 104.0, "r3": 106.0,
          "s1": 98.0, "s2": 96.0, "s3": 94.0}


# ── F1 recorrido del índice ──────────────────────────────────────────────
def test_f1_pegado_a_resistencia_es_negativo():
    # last casi en R1, viniendo desde abajo, en el máximo del día → sin nafta arriba.
    f = factor_recorrido_indice(101.9, PIVOTS, high_dia=101.9, low_dia=100.0)
    assert f is not None and f < 0


def test_f1_pegado_a_soporte_es_positivo():
    f = factor_recorrido_indice(98.1, PIVOTS, high_dia=100.0, low_dia=98.1)
    assert f is not None and f > 0


def test_f1_en_el_medio_es_neutro():
    # Equidistante entre PP y R1, mitad del rango del día.
    f = factor_recorrido_indice(101.0, PIVOTS, high_dia=102.0, low_dia=100.0)
    assert f is not None and abs(f) < 0.3


def test_f1_sin_pivots_none():
    assert factor_recorrido_indice(100.0, {}, 101.0, 99.0) is None


# ── F2 alineación ────────────────────────────────────────────────────────
def test_f2_alineados_arriba_con_corr_alta_empuja_long():
    f = factor_alineacion(0.4, 0.4, corr=0.9)
    assert f is not None and f > 0.4


def test_f2_divergencia_castiga_direccion_del_papel():
    # Papel arriba del PP, índice abajo → el movimiento no está confirmado.
    f = factor_alineacion(0.4, -0.4, corr=0.9)
    assert f is not None and f < 0


def test_f2_corr_baja_apaga_el_factor():
    fuerte = factor_alineacion(0.4, 0.4, corr=0.9)
    debil = factor_alineacion(0.4, 0.4, corr=0.1)
    assert abs(debil) < abs(fuerte)


def test_f2_sin_corr_none():
    assert factor_alineacion(0.4, 0.4, corr=None) is None


# ── F3 nafta del papel ───────────────────────────────────────────────────
def test_f3_rango_gastado_castiga_perseguir():
    # Rango de hoy 1.5× su costumbre, viene subiendo → castiga el LONG.
    f = factor_nafta_papel(3.0, 2.0, direccion_actual=1)
    assert f is not None and f < 0


def test_f3_con_nafta_apoya_leve():
    f = factor_nafta_papel(0.5, 2.0, direccion_actual=1)
    assert f is not None and 0 < f <= 0.4


def test_f3_sin_costumbre_none():
    assert factor_nafta_papel(1.0, None, 1) is None


# ── F4 confluencia ───────────────────────────────────────────────────────
def test_zonas_agrupan_niveles_cercanos():
    frames = {
        "diario": {"r1": 100.0},
        "semanal": {"pp": 100.2},   # < 0.35% de 100 → misma zona
        "mensual": {"r2": 120.0},   # lejos → zona propia
    }
    zonas = zonas_confluencia(frames)
    assert len(zonas) == 2
    assert zonas[0]["fuerza"] == 2


def test_f4_soporte_fuerte_abajo_es_positivo():
    zonas = [{"precio": 99.5, "fuerza": 3, "niveles": []}]
    f = factor_confluencia(100.0, zonas)
    assert f is not None and f > 0


def test_f4_resistencia_fuerte_arriba_es_negativa():
    zonas = [{"precio": 100.5, "fuerza": 3, "niveles": []}]
    f = factor_confluencia(100.0, zonas)
    assert f is not None and f < 0


def test_f4_zona_lejana_no_pesa():
    zonas = [{"precio": 110.0, "fuerza": 4, "niveles": []}]
    assert factor_confluencia(100.0, zonas) == 0.0


# ── Score combinado ──────────────────────────────────────────────────────
def test_score_todo_positivo_da_long():
    res = score_estrategia(
        {"recorrido_indice": 0.8, "alineacion": 0.6, "nafta_papel": 0.3,
         "confluencia": 0.5}, PESOS_V1)
    assert res["direccion"] == "LONG"
    assert res["score"] > 40
    assert res["cobertura"] == 1.0


def test_score_none_redistribuye_no_diluye():
    # Solo un factor con dato, bien positivo → el score NO queda licuado.
    res = score_estrategia(
        {"recorrido_indice": 0.8, "alineacion": None, "nafta_papel": None,
         "confluencia": None}, PESOS_V1)
    assert res["score"] == 80.0
    assert res["cobertura"] < 1.0


def test_score_sin_factores_neutro():
    res = score_estrategia(
        {"recorrido_indice": None, "alineacion": None, "nafta_papel": None,
         "confluencia": None}, PESOS_V1)
    assert res["score"] == 0.0
    assert res["direccion"] == "NEUTRO"
    assert res["cobertura"] == 0.0


def test_score_clamp_en_100():
    res = score_estrategia(
        {"recorrido_indice": 1.0, "alineacion": 1.0, "nafta_papel": 1.0,
         "confluencia": 1.0}, PESOS_V1)
    assert res["score"] == 100.0


# ── medir() del resolver (semántica direccional, sin look-ahead) ─────────
def test_resolver_medir_direccional():
    from datetime import UTC, datetime, timedelta

    from jobs.estrategia_resolver import medir
    t0 = datetime(2026, 7, 29, 14, 0, tzinfo=UTC)
    minutos = [(t0 + timedelta(minutes=i), px)
               for i, px in enumerate([100.0, 100.3, 100.8, 100.2])]
    # LONG desde 100: sube a 100.8 (MFE 0.8) y cierra 100.2 → gana.
    m = medir(100.0, "LONG", minutos, hasta_min=30)
    assert m is not None
    assert m["gano"] is True
    assert m["mfe_pct"] == 0.8
    assert m["toco_objetivo"] is True  # 0.8 ≥ objetivo 0.5 antes que el stop
    # SHORT con el mismo camino: pierde.
    m2 = medir(100.0, "SHORT", minutos, hasta_min=30)
    assert m2 is not None and m2["gano"] is False


def test_resolver_medir_respeta_horizonte():
    from datetime import UTC, datetime, timedelta

    from jobs.estrategia_resolver import medir
    t0 = datetime(2026, 7, 29, 14, 0, tzinfo=UTC)
    # El pico llega DESPUÉS del horizonte → no debe contarlo.
    minutos = [(t0, 100.0), (t0 + timedelta(minutes=5), 100.1),
               (t0 + timedelta(minutes=40), 105.0)]
    m = medir(100.0, "LONG", minutos, hasta_min=15)
    assert m is not None
    assert m["mfe_pct"] == 0.1
