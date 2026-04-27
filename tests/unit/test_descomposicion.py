"""Tests del cálculo de descomposición de retorno (Lecap / Boncap / Lecer).

No tocan Mongo: pruebo `_descomponer_un_bono` y `_interpolar` directos sobre
inputs sintéticos. Las funciones de alto nivel (descomposicion_realizada,
rolldown_esperado) se testean en integración cuando hay Atlas disponible.

Nota sobre la firma — desde el refactor que sumó CER, `_descomponer_un_bono`
es genérico:
  - tasa_fija → valor=precio_sucio, tasa=TEM, freq_dias=30
  - cer       → valor=paridad,      tasa=TEA, freq_dias=365
"""
from __future__ import annotations

import pytest

from api.services.descomposicion_retorno import (
    _descomponer_un_bono,
    _interpolar,
)


# Helpers para no repetir kwargs en cada test de tasa_fija.
def _desc_tf(**kw):
    return _descomponer_un_bono(freq_dias=30, **kw)


# ─────────────────────────────────────────────
# _interpolar
# ─────────────────────────────────────────────


def test_interpolar_lineal_entre_dos_puntos():
    pts = [(30, 0.0184), (90, 0.0193)]
    # mitad exacta: 60 días
    y = _interpolar(pts, 60, "lineal")
    assert abs(y - (0.0184 + 0.0193) / 2) < 1e-9


def test_interpolar_lineal_extrapolacion_arriba():
    pts = [(30, 0.0184), (90, 0.0193), (152, 0.0196)]
    # 200 días — afuera por arriba, extrapola con [90, 152]
    y = _interpolar(pts, 200, "lineal")
    assert 0.0197 < y < 0.0199


def test_interpolar_lineal_target_en_punto_exacto():
    pts = [(30, 0.0184), (90, 0.0193), (152, 0.0196)]
    assert _interpolar(pts, 90, "lineal") == pytest.approx(0.0193)


def test_interpolar_cuadratica_requiere_3_puntos():
    pts = [(30, 0.0184), (90, 0.0193)]
    assert _interpolar(pts, 60, "cuadratica") is None


def test_interpolar_cuadratica_en_punto_aproxima_y():
    pts = [(30, 0.0184), (90, 0.0193), (152, 0.0196), (182, 0.0199), (270, 0.0202)]
    y = _interpolar(pts, 90, "cuadratica")
    assert abs(y - 0.0193) < 5e-4


def test_interpolar_un_punto_solo_devuelve_none():
    assert _interpolar([(90, 0.0193)], 60, "lineal") is None


# ─────────────────────────────────────────────
# _descomponer_un_bono — caso S30S6 del PDF (tasa_fija)
# ─────────────────────────────────────────────


# Curva inicial del ejemplo: TEMs por plazo en días.
_CURVA_INI = [
    (30,  0.0184),
    (90,  0.0193),
    (152, 0.0196),
    (182, 0.0199),
    (270, 0.0202),
]


def test_s30s6_carry_exacto():
    """Carry para 30 días con TEM 1.99% = (1.0199)^1 - 1 = 0.0199."""
    out = _desc_tf(
        valor_ini=115.50, tasa_ini=0.0199,
        vto_dias_ini=182, vto_dias_fin=152,
        valor_fin=118.30, curva_ini=_CURVA_INI, metodo="lineal",
    )
    assert out is not None
    assert out["carry"] == pytest.approx(0.0199, abs=1e-4)


def test_s30s6_r_total():
    out = _desc_tf(
        valor_ini=115.50, tasa_ini=0.0199,
        vto_dias_ini=182, vto_dias_fin=152,
        valor_fin=118.30, curva_ini=_CURVA_INI, metodo="lineal",
    )
    assert out["r_total"] == pytest.approx(0.02424, abs=1e-4)


def test_s30s6_componentes_suman_total():
    """Identidad: r_total = carry + rolldown + cambio_tasa."""
    out = _desc_tf(
        valor_ini=115.50, tasa_ini=0.0199,
        vto_dias_ini=182, vto_dias_fin=152,
        valor_fin=118.30, curva_ini=_CURVA_INI, metodo="lineal",
    )
    suma = out["carry"] + out["rolldown"] + out["cambio_tasa"]
    assert suma == pytest.approx(out["r_total"], abs=1e-6)


def test_s30s6_tem_interpolada_en_152_dias():
    """En la curva inicial, TEM a 152 días es 1.96% (punto exacto)."""
    out = _desc_tf(
        valor_ini=115.50, tasa_ini=0.0199,
        vto_dias_ini=182, vto_dias_fin=152,
        valor_fin=118.30, curva_ini=_CURVA_INI, metodo="lineal",
    )
    assert out["tasa_curva_ini_at_dias_fin"] == pytest.approx(0.0196, abs=1e-4)


def test_curva_quieta_genera_cambio_tasa_cero():
    """Si precio_fin coincide con el precio implicado por la curva inicial al
    plazo final (curva no se movió), cambio_tasa debe ser 0."""
    valor_ini = 115.50
    tasa_ini = 0.0199
    d_ini, d_fin = 182, 152
    flujo = valor_ini * (1 + tasa_ini) ** (d_ini / 30)
    tasa_at_fin = 0.0196
    valor_fin = flujo / (1 + tasa_at_fin) ** (d_fin / 30)

    out = _desc_tf(
        valor_ini=valor_ini, tasa_ini=tasa_ini,
        vto_dias_ini=d_ini, vto_dias_fin=d_fin,
        valor_fin=valor_fin, curva_ini=_CURVA_INI, metodo="lineal",
    )
    assert out["cambio_tasa"] == pytest.approx(0.0, abs=1e-6)


# ─────────────────────────────────────────────
# Edge cases (tasa_fija)
# ─────────────────────────────────────────────


def test_periodo_invertido_devuelve_none():
    out = _desc_tf(
        valor_ini=100, tasa_ini=0.02,
        vto_dias_ini=100, vto_dias_fin=120,
        valor_fin=100, curva_ini=_CURVA_INI, metodo="lineal",
    )
    assert out is None


def test_precios_negativos_devuelven_none():
    out = _desc_tf(
        valor_ini=-1, tasa_ini=0.02,
        vto_dias_ini=100, vto_dias_fin=70,
        valor_fin=100, curva_ini=_CURVA_INI, metodo="lineal",
    )
    assert out is None


def test_bono_que_vence_dentro_del_periodo_devuelve_none():
    """vto_dias_fin <= 0: el bono ya venció al final del período."""
    out = _desc_tf(
        valor_ini=100, tasa_ini=0.02,
        vto_dias_ini=20, vto_dias_fin=0,
        valor_fin=100, curva_ini=_CURVA_INI, metodo="lineal",
    )
    assert out is None


# ─────────────────────────────────────────────
# CER — caso sintético: paridad + TEA real, freq_dias=365
# ─────────────────────────────────────────────


# Curva CER inicial: TEAs reales por plazo en días (zero coupon Lecers).
_CURVA_CER_INI = [
    (60,  0.08),
    (180, 0.10),
    (365, 0.11),
    (540, 0.115),
]


def test_cer_componentes_suman_total():
    """Identidad r_total = carry + rolldown + cambio_tasa para CER."""
    out = _descomponer_un_bono(
        valor_ini=85.0, valor_fin=86.0,   # paridad
        tasa_ini=0.10,                     # TEA real
        vto_dias_ini=365, vto_dias_fin=335,
        curva_ini=_CURVA_CER_INI, metodo="lineal",
        freq_dias=365,
    )
    assert out is not None
    suma = out["carry"] + out["rolldown"] + out["cambio_tasa"]
    assert suma == pytest.approx(out["r_total"], abs=1e-6)


def test_cer_carry_30_dias_a_tea_10pct():
    """Carry 30 días con TEA 10% = (1.10)^(30/365) - 1 ≈ 0.00785."""
    out = _descomponer_un_bono(
        valor_ini=85.0, valor_fin=85.0,
        tasa_ini=0.10,
        vto_dias_ini=365, vto_dias_fin=335,
        curva_ini=_CURVA_CER_INI, metodo="lineal",
        freq_dias=365,
    )
    expected = (1.10) ** (30 / 365) - 1
    assert out["carry"] == pytest.approx(expected, abs=1e-6)


def test_cer_curva_quieta_cambio_tasa_cero():
    """Mismo invariante que tasa_fija: si la curva no se movió, cambio_tasa=0."""
    valor_ini = 85.0
    tasa_ini = 0.10
    d_ini, d_fin = 365, 335
    flujo = valor_ini * (1 + tasa_ini) ** (d_ini / 365)
    # Interpolar en 335 días dentro de _CURVA_CER_INI (entre 180 y 365).
    tasa_at_fin = _interpolar(_CURVA_CER_INI, d_fin, "lineal")
    valor_fin = flujo / (1 + tasa_at_fin) ** (d_fin / 365)

    out = _descomponer_un_bono(
        valor_ini=valor_ini, valor_fin=valor_fin,
        tasa_ini=tasa_ini,
        vto_dias_ini=d_ini, vto_dias_fin=d_fin,
        curva_ini=_CURVA_CER_INI, metodo="lineal",
        freq_dias=365,
    )
    assert out["cambio_tasa"] == pytest.approx(0.0, abs=1e-6)
