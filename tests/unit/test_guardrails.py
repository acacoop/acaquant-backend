"""Tests de los checks PUROS de guardrails (jobs/guardrails.py) — sin SQL ni red.

Congelan el contrato de cada invariante: input dict → violación esperada, y la
semántica de calibración (umbral None = mide pero NUNCA viola).
Doc: docs/RUNBOOK.md (commit 2).
"""
from __future__ import annotations

from jobs.guardrails import (
    check_aum_delta,
    check_completitud_curvas,
    check_especies_cruzadas,
    check_saltos_precio,
    check_sanidad_cierre,
)


def test_aum_delta_dentro_y_fuera_del_umbral():
    (ok,) = check_aum_delta(105.0, 100.0, umbral_pct=10.0)   # +5% con umbral 10
    assert ok["ok"] is True and ok["valor_medido"] == 5.0
    (v,) = check_aum_delta(120.0, 100.0, umbral_pct=10.0)    # +20% con umbral 10
    assert v["ok"] is False and v["check_id"] == "aum_delta" and v["severidad"] == "alta"
    assert "20.00%" in v["mensaje"]


def test_aum_delta_sin_calibrar_no_viola():
    # REGLA #2: umbral None = mide (para el report) pero jamás marca violación
    (r,) = check_aum_delta(200.0, 100.0, umbral_pct=None)
    assert r["ok"] is True and r["valor_medido"] == 100.0 and r["umbral"] is None


def test_aum_delta_sin_datos_es_violacion():
    (r,) = check_aum_delta(None, 100.0, umbral_pct=10.0)
    assert r["ok"] is False and "sin datos" in r["mensaje"]


def test_saltos_precio_detecta_y_reporta_maximo():
    hoy = {"AL30": 110.0, "GD30": 100.5, "NUEVO": 50.0}      # NUEVO sin previo → se ignora
    prev = {"AL30": 100.0, "GD30": 100.0}
    vs = check_saltos_precio(hoy, prev, umbral_pct=5.0)
    assert len(vs) == 1 and vs[0]["ok"] is False
    assert "AL30" in vs[0]["mensaje"] and vs[0]["valor_medido"] == 10.0
    # sin umbral: un solo resultado OK con el máximo medido (calibración)
    (r,) = check_saltos_precio(hoy, prev, umbral_pct=None)
    assert r["ok"] is True and r["valor_medido"] == 10.0


def test_saltos_precio_ignora_precios_invalidos():
    vs = check_saltos_precio({"X": 0.0, "Y": 100.0}, {"X": 100.0, "Y": -1.0},
                             umbral_pct=1.0)
    (r,) = vs
    assert r["ok"] is True   # nada comparable → solo el resumen de calibración


def test_completitud_curvas():
    master = {"cer": {"TX26", "TX28", "TZX27", "TZX28"}, "tasa_fija": {"S30S6"}}
    cierre = {"TX26", "TX28", "TZX27", "S30S6"}
    rs = check_completitud_curvas(master, cierre, umbral_pct=80.0)
    por_curva = {r["mensaje"].split(":")[0]: r for r in rs}
    assert por_curva["curva cer"]["ok"] is False        # 3/4 = 75% < 80
    assert por_curva["curva cer"]["valor_medido"] == 75.0
    assert por_curva["curva tasa_fija"]["ok"] is True   # 1/1
    # sin calibrar: mide pero no viola
    assert all(r["ok"] for r in check_completitud_curvas(master, cierre, None))


def test_sanidad_cierre_absoluta():
    sanas = [{"ticker_corto": "AL30", "ultimo_precio": 100.0}]
    (r,) = check_sanidad_cierre(sanas)
    assert r["ok"] is True
    rotas = sanas + [
        {"ticker_corto": "GD30", "ultimo_precio": 0.0},      # precio cero
        {"ticker_corto": None, "ultimo_precio": 50.0},        # sin ticker
        {"ticker_corto": "TX26", "ultimo_precio": None},      # null
    ]
    (v,) = check_sanidad_cierre(rotas)
    assert v["ok"] is False and v["valor_medido"] == 3 and v["severidad"] == "alta"
    assert "GD30" in v["mensaje"]


# ── especies cruzadas (incidente 2026-08-15: AO29 mostraba ~141.430) ──────────

def _bono(tk, moneda, default, disponibles):
    return {"ticker": tk, "moneda_eje": moneda, "especie_default": default,
            "disponibles": disponibles}


def test_bono_en_usd_apuntando_a_pesos_es_cruce():
    r = check_especies_cruzadas(
        [_bono("AO29", "USD", "pesos", ["pesos", "mep", "cable"])], 0)
    assert not r[0]["ok"] and r[0]["valor_medido"] == 1
    assert "AO29" in r[0]["mensaje"]


def test_on_sin_pata_en_dolares_NO_es_cruce():
    """El falso positivo que marcó 137 de 221: una ON hard dollar que solo cotiza
    en su especie en pesos no tiene a dónde apuntar — no es un error."""
    r = check_especies_cruzadas(
        [_bono("AER9O", "USD", "pesos", ["pesos"])], 0)
    assert r[0]["ok"] and r[0]["valor_medido"] == 0


def test_el_que_ya_usa_la_pata_correcta_no_cuenta():
    r = check_especies_cruzadas(
        [_bono("AL30D", "USD", "mep", ["pesos", "mep", "cable"]),
         _bono("TX26", "ARS", "pesos", ["pesos", "mep"])], 0)
    assert r[0]["ok"] and r[0]["valor_medido"] == 0


def test_sin_umbral_calibrado_reporta_pero_no_viola():
    r = check_especies_cruzadas(
        [_bono("AO29", "USD", "pesos", ["pesos", "mep"])], None)
    assert r[0]["ok"] and r[0]["valor_medido"] == 1   # mide, no marca
