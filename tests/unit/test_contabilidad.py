"""Tests del cálculo puro de CONTABILIDAD (api/services/contabilidad_sql.py).

Congelan las tres reglas del módulo (redefinidas por el back office 2026-09-01):
  1. LO QUE SE COMPRA Y NO SE VENDE NO ES RESULTADO DEL MES. Su valuación final
     no entra en ningún canal — es el saldo inicial del mes que viene.
  2. DOS canales calculados, no un residuo: TENENCIA = los nominales del saldo
     inicial que SOBREVIVIERON al FIFO × Δ precio implícito; INTERMEDIACIÓN =
     el realizado del FIFO. El total es la SUMA de los dos (+ rentas).
  3. UN SOLO MOTOR: la fila del resumen y su modal salen de la misma pasada de
     `libro()`, así no pueden dar números distintos (REGLA #9).
"""
from __future__ import annotations

import pytest

from api.services.contabilidad_sql import calcular_titulos, ledger_fifo, libro, separar_altas

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


def test_comprado_y_no_vendido_no_es_resultado():
    """LA REGLA (back office, 2026-09-01). Se compró a 600k y al cierre vale
    620k: esos 20k NO son resultado de este mes — no se realizó nada y no había
    posición al inicio. El modelo viejo los cantaba como intermediación."""
    filas = _calc([], [_t("[100] AL30 - GD", 1_000_000, 620_000)],
                  [_b("compra", "AL30", 1_000_000, -600_000)])
    (f,) = filas
    assert f["estado"] == "alta"
    assert f["rxt"] == 0             # no había nada al inicio
    assert f["intermediacion"] == 0  # no se vendió nada
    assert f["total"] == 0
    assert f["compras"] == pytest.approx(600_000)  # la compra igual se informa
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
    """La compra en USD se pesifica con el mep del boleto. Y como no se vendió,
    la valuación final (1,4M contra 1,3M invertidos) no genera resultado."""
    filas = _calc([], [_t("[100] AL30 - GD", 1_000, 1_400_000)],
                  [_b("compra", "AL30", 1_000, -1_000, moneda="USD", mep=1_300.0)])
    (f,) = filas
    assert f["compras"] == pytest.approx(1_300_000)
    assert f["total"] == 0


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


def test_total_es_la_suma_de_los_canales():
    """Mes revuelto (compra + venta + renta): el total es la SUMA de los canales
    calculados, no una identidad repartida. Y la TENENCIA va sobre los
    nominales del saldo INICIAL que sobrevivieron al FIFO — no sobre
    `min(ini, fin)` de la planilla vieja."""
    filas = _calc(
        [_t("[100] AL30 - GD", 3_000_000, 1_500_000)],   # px 0,50
        [_t("[100] AL30 - GD", 2_500_000, 1_400_000)],   # px 0,56
        [_b("compra", "AL30", 500_000, -260_000),
         _b("venta", "AL30", 1_000_000, 545_000),
         _b("acreencia", "AL30", 0, 30_000)])
    (f,) = filas
    # Del saldo inicial (3M) el FIFO vendió 1M → sobreviven 2M, NO los 2,5M que
    # daría min(3M, 2,5M): los otros 500k se compraron a mitad de mes y no
    # pueden devengar el rendimiento del mes completo.
    assert f["rxt"] == pytest.approx(2_000_000 * (0.56 - 0.50))    # 120.000
    assert f["rxt"] != pytest.approx(2_500_000 * (0.56 - 0.50))    # la planilla vieja
    # Realizado FIFO: 545.000 − costo del millón más viejo (1,5M × 1/3)
    assert f["intermediacion"] == pytest.approx(545_000 - 500_000)  # 45.000
    assert f["rentas"] == pytest.approx(30_000)
    assert f["total"] == pytest.approx(120_000 + 45_000 + 30_000)   # 195.000
    assert f["rxt"] + f["intermediacion"] + f["rentas"] == pytest.approx(f["total"])
    assert f["cuadra"]  # −500k = +500k − 1M


def test_ao29_la_valuacion_nueva_no_es_ganancia():
    """REGRESIÓN del caso que rompió el módulo (cuenta propia, AO29, 08/26).

    Arrancó SIN el título, compró 815.604 y los vendió, después vendió 439.407
    sin tenerlos y los recompró. Realizado FIFO: 1.171.799. Pero la foto del
    cierre trae 955.084 nominales valuados en 1.324.988.033 que NINGÚN boleto
    explica — y el modelo viejo (total = ΔValuación + ventas − compras) los
    cantaba como intermediación: **1.325.814.415**, mil veces el resultado real.
    """
    bol = [_b("compra", "AL30", q, -i) for q, i in
           ((34_404, 49_541_760), (49_535, 71_256_098), (56_412, 81_148_662),
            (631_597, 908_552_285), (43_656, 62_799_156))]
    bol += [_b("venta", "AL30", 815_604, 1_174_469_760),
            _b("venta", "AL30", 439_407, 615_000_145),
            _b("compra", "AL30", 439_407, -615_345_563)]
    (f,) = _calc([], [_t("[100] AL30 - GD", 955_084, 1_324_988_033)], bol)
    assert f["rxt"] == 0                                    # no había nada al inicio
    assert f["intermediacion"] == pytest.approx(1_171_799, abs=1)
    assert f["total"] == pytest.approx(1_171_799, abs=1)
    # La identidad vieja daba esto, y la valuación final NO puede aparecer:
    assert f["total"] != pytest.approx(1_325_814_415, abs=1)
    assert not f["cuadra"] and f["cuadre_nominales"] == pytest.approx(955_084)
    assert f["sin_costo"] == 1   # la venta de 439.407 sin lote queda declarada


def test_el_modal_y_la_fila_dan_lo_mismo():
    """REGLA #9: un solo motor. `libro()` es el que corre en las dos puntas, así
    que el `pnl_acum` de la última fila del modal == intermediación + rentas de
    la fila del resumen. Hasta 2026-09-01 eran cálculos distintos y para AO29
    daban 1.325.814.415 contra 1.171.799 sin que nada los comparara."""
    bol = [_b("compra", "AL30", 500_000, -260_000),
           _b("venta", "AL30", 1_000_000, 545_000),
           _b("acreencia", "AL30", 0, 30_000)]
    (f,) = _calc([_t("[100] AL30 - GD", 3_000_000, 1_500_000)],
                 [_t("[100] AL30 - GD", 2_500_000, 1_400_000)], bol)
    filas, _ = libro(key="AL30", qty_ini=3_000_000, v_ini=1_500_000,
                     fecha_ini="2026-07-31", boletos=bol)
    assert filas[0]["categoria"] == "saldo_inicial"      # la posición inicial primero
    assert filas[-1]["pnl_acum"] == pytest.approx(f["intermediacion"] + f["rentas"])


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
