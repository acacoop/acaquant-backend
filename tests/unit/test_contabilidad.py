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

from api.services.contabilidad_sql import calcular_titulos, ledger_fifo, separar_altas

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


def test_alta_pura_se_separa_del_informe():
    """Comprado para dejar en cartera (0 → X, solo compras): no es resultado de
    ESTE mes — va al bloque ALTAS y no ensucia los totales. Una alta que además
    VENDIÓ en el mes sí tiene resultado y se queda en el informe."""
    filas = _calc(
        [],
        [_t("[100] AL30 - GD", 1_000_000, 620_000),
         _t("[200] FCI X", 500, 5_100, cartera="FCI")],
        [_b("compra", "AL30", 1_000_000, -600_000),
         _b("compra", "CAFCI99", 1_000, -10_000),
         _b("rescate_fci", "CAFCI99", 500, 5_050)])
    con_resultado, altas = separar_altas(filas)
    assert [t["titulo"] for t in altas] == ["AL30"]          # alta pura → afuera
    assert [t["titulo"] for t in con_resultado] == ["FCI X"]  # alta que vendió → queda


def test_direccion_del_catalogo():
    """El enrich `operacion` de operaciones.operaciones → compra/venta/None.
    Cauciones, futuros y `otro` no mueven posición de títulos."""
    from api.services.contabilidad_sql import _direccion
    assert _direccion("compra") == "compra"
    assert _direccion("Venta") == "venta"
    assert _direccion("suscripción FCI") == "compra"
    assert _direccion("rescate FCI") == "venta"
    for x in ("caución tomadora", "caución colocadora", "futuros", "otro", "", None):
        assert _direccion(x) is None


def test_direccion_respaldo_por_tipo_operacion():
    """Caso TTCBO (2026-09-01): el catálogo no definía la punta de la venta
    SENEBI y el boleto quedaba afuera del cálculo teniéndolo a la vista.
    Cuando el enrich no resuelve, la punta se lee del descriptor de Aunesa —
    salvo cauciones/futuros/opciones, que nunca mueven nominales de títulos."""
    from api.services.contabilidad_sql import _direccion
    assert _direccion("otro", "SENEBI Contado - Venta") == "venta"
    assert _direccion(None, "Concurrencia Contado - Venta") == "venta"
    assert _direccion("otro", "SENEBI Contado - Compra") == "compra"
    assert _direccion("otro", "COLP - Licitación") == "compra"  # primario = compra
    assert _direccion("licitacion", None) == "compra"
    assert _direccion("otro", "Caución Colocadora - Venta") is None
    assert _direccion("otro", "Futuros - Compra") is None
    assert _direccion("venta", "lo que sea") == "venta"  # el catálogo manda si resuelve


def test_boleto_sin_direccion_no_mueve_nada():
    """Un boleto con categoria None (caución/futuro/otro) no toca compras,
    ventas ni el cuadre — se declara en `ignorados`, no se suma."""
    filas = _calc([_t("[100] AL30 - GD", 1_000, 500)],
                  [_t("[100] AL30 - GD", 1_000, 510)],
                  [_b(None, "AL30", 999, 12_345)])
    (f,) = filas
    assert f["compras"] == 0 and f["ventas"] == 0
    assert f["rxt"] == pytest.approx(10)
    assert f["cuadra"] and f["n_boletos"] == 0


def _mov(categoria, cantidad, importe_ars):
    return {"categoria": categoria, "cantidad": cantidad, "importe_ars": importe_ars}


def test_ledger_fifo_realiza_por_lotes_viejos():
    """Dos compras a precio distinto, venta parcial: el costo sale del lote MÁS
    VIEJO (FIFO), y los acumulados van fila por fila."""
    movs = [_mov("compra", 100, 1_000),   # lote 1: 10 $/nominal
            _mov("compra", 100, 2_000),   # lote 2: 20 $/nominal
            _mov("venta", 150, 2_400)]    # 16 $/nominal vendido
    stats = ledger_fifo(movs)
    assert [m["nominales_acum"] for m in movs] == [100, 200, 50]
    # costo FIFO: 100 del lote 1 (1.000) + 50 del lote 2 (1.000) = 2.000
    assert movs[-1]["pnl_acum"] == pytest.approx(2_400 - 2_000)
    assert stats["sin_costo"] == 0


def test_ledger_fifo_venta_sin_lote_se_marca():
    """Venta que excede lo comprado en el libro (posición pre-data): solo la
    parte con lote realiza PnL y la fila queda marcada `sin_costo`."""
    movs = [_mov("compra", 100, 1_000),
            _mov("venta", 200, 3_000)]    # 15 $/nominal, la mitad sin costo
    stats = ledger_fifo(movs)
    assert movs[-1].get("sin_costo") is True
    assert stats["sin_costo"] == 1
    # cubierta = 100/200 → ingresa 1.500, costo 1.000 → +500
    assert movs[-1]["pnl_acum"] == pytest.approx(500)
    assert movs[-1]["nominales_acum"] == pytest.approx(-100)


def test_ledger_arranca_de_la_posicion_inicial():
    """El libro del mes: la POSICIÓN INICIAL (nominales + valuación del cierre
    anterior) entra como primer lote sin generar PnL, y vender el 100% realiza
    `venta − valuación inicial` — la intermediación de la fila del resumen."""
    movs = [_mov("saldo_inicial", 58_900, 93_745_240),
            _mov("venta", 58_900, 94_289_476)]
    stats = ledger_fifo(movs)
    assert movs[0]["pnl_acum"] == 0 and movs[0]["nominales_acum"] == 58_900
    assert movs[1]["pnl_acum"] == pytest.approx(94_289_476 - 93_745_240)
    assert movs[1]["nominales_acum"] == 0
    assert stats["sin_costo"] == 0


def test_ledger_fifo_rentas_y_otros():
    """La renta suma con signo al acumulado sin tocar nominales; un boleto sin
    dirección (caución/futuro) no toca nada pero muestra el acumulado vigente."""
    movs = [_mov("compra", 100, 1_000),
            _mov("acreencia", 0, 250),
            _mov(None, 999, 99_999)]
    ledger_fifo(movs)
    assert movs[1]["nominales_acum"] == 100 and movs[1]["pnl_acum"] == 250
    assert movs[2]["nominales_acum"] == 100 and movs[2]["pnl_acum"] == 250


def test_mes_contable_por_liquidacion():
    """Detección del user 2026-09-01: la tenencia es una foto LIQUIDADA y los
    boletos van por CONCERTACIÓN. Un 24hs del último hábil del mes anterior
    liquida en ESTE mes (entra); un 24hs del último hábil de ESTE mes liquida
    el mes que viene (sale). Contado inmediato queda donde concertó."""
    from api.services.contabilidad_sql import pertenece_al_mes
    kw = {"mes": "2026-08", "borde_prev": "2026-07-31", "borde_fin": "2026-08-31"}
    # borde del mes anterior
    assert pertenece_al_mes("2026-07-31", "24hs", **kw)          # liquida 1/8 → agosto
    assert not pertenece_al_mes("2026-07-31", "Contado Inmediato", **kw)
    assert not pertenece_al_mes("2026-07-30", "24hs", **kw)      # liquida 31/7 → julio
    # adentro del mes
    assert pertenece_al_mes("2026-08-14", "24hs", **kw)
    assert pertenece_al_mes("2026-08-14", "Contado Inmediato", **kw)
    # borde de este mes
    assert not pertenece_al_mes("2026-08-31", "24hs", **kw)      # liquida 1/9 → septiembre
    assert pertenece_al_mes("2026-08-31", "Contado Inmediato", **kw)


def test_orden_por_impacto():
    filas = _calc(
        [_t("[100] AL30 - GD", 1_000, 1_000), _t("[200] FCI X", 100, 1_000, cartera="FCI")],
        [_t("[100] AL30 - GD", 1_000, 1_010), _t("[200] FCI X", 100, 2_000, cartera="FCI")],
        [])
    assert [f["titulo"] for f in filas] == ["FCI X", "AL30"]
