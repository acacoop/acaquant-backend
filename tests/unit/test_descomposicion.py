"""Tests del cálculo de descomposición de retorno (Lecap / Boncap).

No tocan Mongo: pruebo `_descomponer_un_bono` y `_interpolar` directos sobre
inputs sintéticos. Las funciones de alto nivel (descomposicion_realizada,
rolldown_esperado) se testean en integración cuando hay Atlas disponible.
"""
from __future__ import annotations

import pytest

from api.services.descomposicion_retorno import (
    _descomponer_un_bono,
    _interpolar,
)

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
    # pendiente entre 90 y 152: (0.0196-0.0193)/62 ≈ 4.84e-6
    # y(200) ≈ 0.0196 + (200-152) × 4.84e-6 ≈ 0.0198
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
    # No exacta porque es ajuste cuadrático sobre 5 puntos, pero cerca.
    assert abs(y - 0.0193) < 5e-4


def test_interpolar_un_punto_solo_devuelve_none():
    assert _interpolar([(90, 0.0193)], 60, "lineal") is None


# ─────────────────────────────────────────────
# _descomponer_un_bono — caso S30S6 del PDF
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
    out = _descomponer_un_bono(
        precio_ini=115.50, tem_ini=0.0199,
        vto_dias_ini=182, vto_dias_fin=152,
        precio_fin=118.30, curva_ini=_CURVA_INI, metodo="lineal",
    )
    assert out is not None
    # Carry exacto: (1+0.0199)^(30/30) - 1 = 0.0199
    assert out["carry"] == pytest.approx(0.0199, abs=1e-4)


def test_s30s6_r_total():
    out = _descomponer_un_bono(
        precio_ini=115.50, tem_ini=0.0199,
        vto_dias_ini=182, vto_dias_fin=152,
        precio_fin=118.30, curva_ini=_CURVA_INI, metodo="lineal",
    )
    # 118.30 / 115.50 - 1 ≈ 0.02424
    assert out["r_total"] == pytest.approx(0.02424, abs=1e-4)


def test_s30s6_componentes_suman_total():
    """Identidad: r_total = carry + rolldown + cambio_tasa."""
    out = _descomponer_un_bono(
        precio_ini=115.50, tem_ini=0.0199,
        vto_dias_ini=182, vto_dias_fin=152,
        precio_fin=118.30, curva_ini=_CURVA_INI, metodo="lineal",
    )
    suma = out["carry"] + out["rolldown"] + out["cambio_tasa"]
    assert suma == pytest.approx(out["r_total"], abs=1e-6)


def test_s30s6_tem_interpolada_en_152_dias():
    """En la curva inicial, TEM a 152 días es 1.96% (punto exacto)."""
    out = _descomponer_un_bono(
        precio_ini=115.50, tem_ini=0.0199,
        vto_dias_ini=182, vto_dias_fin=152,
        precio_fin=118.30, curva_ini=_CURVA_INI, metodo="lineal",
    )
    assert out["tem_curva_ini_at_dias_fin"] == pytest.approx(0.0196, abs=1e-4)


def test_curva_quieta_genera_cambio_tasa_cero():
    """Si precio_fin coincide con el precio implicado por la curva inicial al
    plazo final (curva no se movió), cambio_tasa debe ser 0."""
    # Construyo precio_fin exactamente igual al "precio_si_curva_ini" del cálculo
    # interno: precio_fin = flujo_final / (1+tem_at_fin)^(d_fin/30)
    precio_ini = 115.50
    tem_ini = 0.0199
    d_ini, d_fin = 182, 152
    flujo = precio_ini * (1 + tem_ini) ** (d_ini / 30)
    tem_at_fin = 0.0196  # punto exacto en la curva
    precio_fin = flujo / (1 + tem_at_fin) ** (d_fin / 30)

    out = _descomponer_un_bono(
        precio_ini=precio_ini, tem_ini=tem_ini,
        vto_dias_ini=d_ini, vto_dias_fin=d_fin,
        precio_fin=precio_fin, curva_ini=_CURVA_INI, metodo="lineal",
    )
    assert out["cambio_tasa"] == pytest.approx(0.0, abs=1e-6)


# ─────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────


def test_periodo_invertido_devuelve_none():
    """vto_dias_fin > vto_dias_ini implica el período corre al revés."""
    out = _descomponer_un_bono(
        precio_ini=100, tem_ini=0.02,
        vto_dias_ini=100, vto_dias_fin=120,
        precio_fin=100, curva_ini=_CURVA_INI, metodo="lineal",
    )
    assert out is None


def test_precios_negativos_devuelven_none():
    out = _descomponer_un_bono(
        precio_ini=-1, tem_ini=0.02,
        vto_dias_ini=100, vto_dias_fin=70,
        precio_fin=100, curva_ini=_CURVA_INI, metodo="lineal",
    )
    assert out is None


def test_bono_que_vence_dentro_del_periodo_devuelve_none():
    """vto_dias_fin <= 0: el bono ya venció al final del período."""
    out = _descomponer_un_bono(
        precio_ini=100, tem_ini=0.02,
        vto_dias_ini=20, vto_dias_fin=0,
        precio_fin=100, curva_ini=_CURVA_INI, metodo="lineal",
    )
    assert out is None
