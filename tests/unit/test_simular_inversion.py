"""Tests del simulador de inversión (api/services/simular_inversion.py) — puros.

**Qué se protege acá.** El modal SIMULAR INVERSIÓN promete dos cosas: que la
tasa al precio simulado sale del MISMO motor que la tabla (`calcular_campos`
con el precio inyectado — acá corre el motor de verdad, no un mock), y que el
cronograma escala bien al importe (`vn = importe × 100 / precio`). Si la escala
se rompiera, el modal diría que con $1M cobrás $150 — un número verosímil y
equivocado, el modo de falla favorito de este repo.

La base se reemplaza por docs sintéticos (find_one / deps / precio de
referencia); la matemática es la real.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from api.services import simular_inversion as si

FUT = (date.today() + timedelta(days=365)).isoformat()
FUT2 = (date.today() + timedelta(days=730)).isoformat()
PAS = (date.today() - timedelta(days=100)).isoformat()

# Días hábiles sintéticos: hoy → +40 días corridos (el settlement T+1 del motor
# solo necesita que exista un hábil después de hoy).
HABILES = [(date.today() + timedelta(days=i)).isoformat() for i in range(40)]

DEPS_SIN_FX = {"cer": {}, "habiles": HABILES, "mep": None, "a3500": None}


def _patch(monkeypatch, doc, deps=DEPS_SIN_FX, last=None):
    monkeypatch.setattr(si.curvas_sql, "find_one", lambda tk: doc)
    monkeypatch.setattr(si, "_deps_calculo", lambda: deps)
    monkeypatch.setattr(si, "_precio_referencia", lambda s: last)


def _lecap(**kw):
    """Bullet tasa fija: paga 158 por 100 VN al vencimiento, sin array de flujos."""
    base = {"ticker_corto": "S30E7", "ticker": "MERV - XMEV - S30E7 - 24hs",
            "emisor_tipo": "soberano", "moneda_eje": "ARS", "ajuste": "fija",
            "valor_nominal": 100, "fecha_vencimiento": FUT,
            "flujo_vencimiento": 158.0}
    base.update(kw)
    return base


def test_lecap_escala_y_tea_del_motor(monkeypatch):
    _patch(monkeypatch, _lecap())
    r = si.simular(ticker="S30E7", importe=1_000_000, precio=100.0)
    sim = r["simulacion"]
    assert r["rama"] == "tasa_fija"
    # $1M a precio 100 → VN 1.000.000 → cobra 158% del VN al vto.
    assert sim["vn_nominal"] == 1_000_000
    assert sim["total_a_cobrar"] == pytest.approx(1_580_000, abs=1)
    assert sim["ganancia"] == pytest.approx(580_000, abs=1)
    assert sim["rendimiento_directo"] == pytest.approx(0.58, abs=0.001)
    # TEA del MOTOR: bullet a ~365 días con retorno directo 58% → TEA ≈ 58%.
    assert sim["metrics"]["TEA"] == pytest.approx(0.58, abs=0.02)
    # TNA/TEM derivadas con quant.tasas (mismas convenciones que la tabla).
    assert sim["metrics"]["TEM"] is not None
    assert sim["metrics"]["TNA"] is not None


def test_bajar_el_precio_sube_la_tasa(monkeypatch):
    """La razón de ser del modal: el precio es un INPUT y la TIR responde."""
    _patch(monkeypatch, _lecap())
    caro = si.simular(ticker="S30E7", importe=100_000, precio=150.0)
    barato = si.simular(ticker="S30E7", importe=100_000, precio=100.0)
    assert barato["simulacion"]["metrics"]["TEA"] > caro["simulacion"]["metrics"]["TEA"]
    # El total cobrado también cambia: a precio más barato el mismo importe
    # compra más nominales.
    assert barato["simulacion"]["total_a_cobrar"] > caro["simulacion"]["total_a_cobrar"]


def test_sin_precio_usa_el_last_de_referencia(monkeypatch):
    _patch(monkeypatch, _lecap(), last=126.4)
    r = si.simular(ticker="S30E7", importe=100_000)
    assert r["precio_referencia"] == 126.4
    assert r["simulacion"]["precio"] == 126.4


def test_sin_precio_ni_last_devuelve_error(monkeypatch):
    _patch(monkeypatch, _lecap(), last=None)
    r = si.simular(ticker="S30E7", importe=100_000)
    assert "error" in r


def test_flujos_pasados_no_se_cobran(monkeypatch):
    """La compra es hoy: un cupón ya pagado no entra al total."""
    doc = _lecap(flujo_vencimiento=None, flujos=[
        {"fecha": PAS, "amortizacion": 0, "interes": 10.0},
        {"fecha": FUT, "amortizacion": 100.0, "interes": 10.0},
    ])
    _patch(monkeypatch, doc)
    r = si.simular(ticker="S30E7", importe=100_000, precio=100.0)
    sim = r["simulacion"]
    assert sim["n_pagos"] == 1
    assert sim["total_a_cobrar"] == pytest.approx(110_000, abs=1)


def test_soberano_peso_cobra_usd_y_convierte_por_mep(monkeypatch):
    """AL30 en pesos: pagás ARS, cobrás USD. La ganancia solo existe vía MEP."""
    doc = {"ticker_corto": "AL30", "ticker": "MERV - XMEV - AL30 - 24hs",
           "emisor_tipo": "soberano", "moneda_eje": "USD", "ajuste": "fija",
           "moneda_flujo": "USD", "ley": "ar",
           "valor_nominal": 100, "fecha_vencimiento": FUT2,
           "flujos": [{"fecha": FUT, "amortizacion_pct": 20.0,
                       "cupon_sobre_residual": 0.4, "residual_previo_pct": 100.0},
                      {"fecha": FUT2, "amortizacion_pct": 80.0,
                       "cupon_sobre_residual": 0.3, "residual_previo_pct": 80.0}]}
    deps = dict(DEPS_SIN_FX, mep=1000.0)
    _patch(monkeypatch, doc, deps=deps)
    # Pata en PESOS (símbolo sin sufijo D/C): el motor divide el precio por MEP.
    r = si.simular(ticker="AL30", importe=1_000_000, precio=80_000.0)
    sim = r["simulacion"]
    assert r["rama"] == "soberanos"
    # $1M a 80.000 por 100 VN → 1.250 VN → cobra 100.7% del VN en USD.
    assert sim["vn_nominal"] == pytest.approx(1250, abs=0.01)
    assert sim["total_a_cobrar"] == pytest.approx(1250 * 1.007, abs=0.1)
    # Importe pesificado a USD por MEP=1000 → 1.000 USD; cobra ~1.258,75 USD.
    assert sim["importe_en_moneda_flujo"] == pytest.approx(1000, abs=0.01)
    assert sim["ganancia"] == pytest.approx(1250 * 1.007 - 1000, abs=0.1)
    assert sim["metrics"]["TEA"] is not None


def test_soberano_sin_mep_avisa_y_no_inventa(monkeypatch):
    doc = {"ticker_corto": "AL30", "ticker": "MERV - XMEV - AL30 - 24hs",
           "emisor_tipo": "soberano", "moneda_eje": "USD", "ajuste": "fija",
           "moneda_flujo": "USD",
           "valor_nominal": 100, "fecha_vencimiento": FUT2,
           "flujos": [{"fecha": FUT2, "amortizacion_pct": 100.0,
                       "cupon_sobre_residual": 0.5, "residual_previo_pct": 100.0}]}
    _patch(monkeypatch, doc)  # sin MEP
    r = si.simular(ticker="AL30", importe=1_000_000, precio=80_000.0)
    assert "mep_faltante" in r["warnings"]
    # Los flujos en USD igual viajan (escalados por VN); lo que no se inventa
    # es la tasa (el motor no puede pasar el precio a USD) ni la ganancia.
    assert r["simulacion"]["n_pagos"] == 1
    assert r["simulacion"]["metrics"]["TEA"] is None


def test_cer_ajusta_por_ultimo_cer_y_avisa_proyeccion(monkeypatch):
    """CER: el cronograma contractual está a valores de EMISIÓN. Acá se ajusta
    (eso es lo que se cobra) y si el CER de liquidación no está publicado se
    proyecta constante con bandera."""
    doc = {"ticker_corto": "TX28", "ticker": "MERV - XMEV - TX28 - 24hs",
           "emisor_tipo": "soberano", "moneda_eje": "ARS", "ajuste": "cer",
           "valor_nominal": 100, "fecha_vencimiento": FUT,
           "cer_emision": 50.0,
           "flujos": [{"fecha": FUT, "amortizacion_pct": 100.0,
                       "cupon_sobre_residual": 0.02, "residual_previo_pct": 100.0}]}
    hoy = date.today().isoformat()
    deps = dict(DEPS_SIN_FX, cer={hoy: 150.0})  # CER se triplicó desde emisión
    _patch(monkeypatch, doc, deps=deps)
    r = si.simular(ticker="TX28", importe=300_000, precio=300.0)
    sim = r["simulacion"]
    assert r["rama"] == "cer"
    # $300k a precio 300 → VN 100.000. Flujo contractual 102 por 100 VN a
    # valores de emisión × factor CER 3 → 306 por 100 VN → $306.000.
    assert sim["vn_nominal"] == pytest.approx(100_000, abs=0.01)
    assert sim["total_a_cobrar"] == pytest.approx(306_000, abs=1)
    assert sim["cer_proyectado"] is True
    assert "cer_proyectado_constante" in r["warnings"]
    assert r["moneda_flujo"] == "ARS"


def test_importe_invalido(monkeypatch):
    _patch(monkeypatch, _lecap())
    assert "error" in si.simular(ticker="S30E7", importe=0)
    assert "error" in si.simular(ticker="", importe=100)
