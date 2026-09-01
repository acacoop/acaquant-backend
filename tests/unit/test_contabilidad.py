"""Tests del cálculo puro de CONTABILIDAD (api/services/contabilidad_sql.py).

Congelan las dos reglas del módulo:
  1. La IDENTIDAD: total = ΔValuación + ventas − compras + rentas, y el split
     rxt + intermediación + rentas == total SIEMPRE (el residuo no puede
     descuadrar por construcción — acá se verifica que de verdad no).
  2. La fórmula de RxT de la planilla del back office: posición mantenida
     (min de nominales) × Δ precio implícito (valuación ÷ nominales).
"""
from __future__ import annotations

import pytest

from api.services.contabilidad_sql import calcular_titulos

_U2M = {"[100] AL30 - GD": "AL30", "[200] FCI X": "CAFCI99", "[201] FCI X CLASE B": "CAFCI99"}
_M2D = {"AL30": "AL30", "CAFCI99": "FCI X"}


def _calc(filas_ini, filas_fin, boletos, u2m=None, m2d=None):
    return calcular_titulos(
        filas_ini=filas_ini, filas_fin=filas_fin, boletos=boletos,
        unidad_to_match=u2m or _U2M, match_to_display=m2d or _M2D)


def _t(unidad, cantidad, valuacion, cartera="HD"):
    return {"unidad": unidad, "cartera": cartera, "cantidad": cantidad, "valuacion": valuacion}


def _b(categoria, ticker, cantidad, importe, moneda="ARS", mep=None):
    return {"fecha": "2026-08-14", "categoria": categoria, "op": categoria,
            "ticker": ticker, "cantidad": cantidad, "importe": importe,
            "moneda": moneda, "mep": mep, "comprobante": "BOL 1"}


def test_sin_operaciones_todo_es_tenencia():
    """Misma cantidad los dos cierres → RxT = ΔValuación, intermediación 0."""
    filas = _calc([_t("[100] AL30 - GD", 2_000_000, 1_000_000)],
                  [_t("[100] AL30 - GD", 2_000_000, 1_150_000)], [])
    (f,) = filas
    assert f["estado"] == "sin_operar"
    assert f["rxt"] == pytest.approx(150_000)
    assert f["intermediacion"] == pytest.approx(0)
    assert f["total"] == pytest.approx(150_000)
    assert f["cuadra"]


def test_venta_parcial_parte_en_dos():
    """2M → 1M: RxT sobre el 1M mantenido; lo vendido va a intermediación."""
    filas = _calc(
        [_t("[100] AL30 - GD", 2_000_000, 1_000_000)],   # px implícito 0.50
        [_t("[100] AL30 - GD", 1_000_000, 600_000)],     # px implícito 0.60
        [_b("venta", "AL30", -1_000_000, 580_000)])      # signo crudo NO confiable
    (f,) = filas
    assert f["rxt"] == pytest.approx(1_000_000 * (0.60 - 0.50))
    # identidad: ΔV + ventas = (600k − 1M) + 580k = 180k
    assert f["total"] == pytest.approx(180_000)
    assert f["intermediacion"] == pytest.approx(180_000 - 100_000)
    assert f["cuadra"]  # qf − qi = −1M y la venta explica −1M


def test_baja_del_periodo_tiene_numero():
    """La celda roja de la planilla: se vendió todo → todo intermediación."""
    filas = _calc([_t("[100] AL30 - GD", 1_000_000, 500_000)], [],
                  [_b("venta", "AL30", 1_000_000, 540_000)])
    (f,) = filas
    assert f["estado"] == "baja"
    assert f["rxt"] == 0
    assert f["total"] == pytest.approx(540_000 - 500_000)
    assert f["intermediacion"] == pytest.approx(40_000)
    assert f["cuadra"]


def test_alta_del_periodo_tiene_numero():
    filas = _calc([], [_t("[100] AL30 - GD", 1_000_000, 620_000)],
                  [_b("compra", "AL30", 1_000_000, -600_000)])
    (f,) = filas
    assert f["estado"] == "alta"
    assert f["rxt"] == 0
    assert f["total"] == pytest.approx(20_000)
    assert f["intermediacion"] == pytest.approx(20_000)
    assert f["cuadra"]


def test_renta_es_su_propio_canal():
    """Cupón cobrado con posición quieta: RxT = ΔV, renta aparte, interm. 0."""
    filas = _calc([_t("[100] AL30 - GD", 1_000_000, 500_000)],
                  [_t("[100] AL30 - GD", 1_000_000, 510_000)],
                  [_b("acreencia", "AL30", 0, 25_000)])
    (f,) = filas
    assert f["rentas"] == pytest.approx(25_000)
    assert f["rxt"] == pytest.approx(10_000)
    assert f["intermediacion"] == pytest.approx(0)
    assert f["total"] == pytest.approx(35_000)


def test_cuadre_detecta_boleto_faltante():
    """Los nominales bajaron y ningún boleto lo explica → la fila no cuadra."""
    filas = _calc([_t("[100] AL30 - GD", 2_000_000, 1_000_000)],
                  [_t("[100] AL30 - GD", 1_500_000, 750_000)], [])
    (f,) = filas
    assert not f["cuadra"]
    assert f["cuadre_nominales"] == pytest.approx(-500_000)


def test_boleto_usd_pesifica_con_su_mep():
    filas = _calc([], [_t("[100] AL30 - GD", 1_000, 1_400_000)],
                  [_b("compra", "AL30", 1_000, -1_000, moneda="USD", mep=1_300.0)])
    (f,) = filas
    assert f["compras"] == pytest.approx(1_300_000)
    assert f["total"] == pytest.approx(100_000)


def test_fci_agrupa_por_cafci_las_dos_unidades():
    """El rebautizo de Aunesa: dos unidades, mismo fondo (mismo CAFCI) → una fila."""
    filas = _calc(
        [_t("[200] FCI X", 100, 1_000, cartera="FCI")],
        [_t("[201] FCI X CLASE B", 100, 1_100, cartera="FCI")],
        [])
    (f,) = filas
    assert f["titulo"] == "FCI X"
    assert sorted(f["unidades"]) == ["[200] FCI X", "[201] FCI X CLASE B"]
    assert f["rxt"] == pytest.approx(100)  # misma cantidad → todo tenencia


def test_split_siempre_suma_el_total():
    """La identidad no es opinable: rxt + intermediación + rentas == total,
    también en un mes revuelto (compra + venta + renta + cantidad distinta)."""
    filas = _calc(
        [_t("[100] AL30 - GD", 3_000_000, 1_500_000)],
        [_t("[100] AL30 - GD", 2_500_000, 1_400_000)],
        [_b("compra", "AL30", 500_000, -260_000),
         _b("venta", "AL30", 1_000_000, 545_000),
         _b("acreencia", "AL30", 0, 30_000)])
    (f,) = filas
    assert f["rxt"] + f["intermediacion"] + f["rentas"] == pytest.approx(f["total"])
    # identidad explícita: ΔV + ventas − compras + rentas
    assert f["total"] == pytest.approx((1_400_000 - 1_500_000) + 545_000 - 260_000 + 30_000)
    assert f["cuadra"]  # −500k = +500k − 1M


def test_orden_por_impacto():
    filas = _calc(
        [_t("[100] AL30 - GD", 1_000, 1_000), _t("[200] FCI X", 100, 1_000, cartera="FCI")],
        [_t("[100] AL30 - GD", 1_000, 1_010), _t("[200] FCI X", 100, 2_000, cartera="FCI")],
        [])
    assert [f["titulo"] for f in filas] == ["FCI X", "AL30"]
