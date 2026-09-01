"""Tests del cálculo puro de CONTABILIDAD (api/services/contabilidad_sql.py).

Congelan las tres reglas del módulo (redefinidas por el back office 2026-09-01):
  1. LO QUE SE COMPRA Y NO SE VENDE NO ES RESULTADO DEL MES. Su valuación final
     no entra en ningún canal — es el saldo inicial del mes que viene.
  2. DOS canales calculados, no un residuo: TENENCIA = `min(nominales_ini,
     nominales_fin)` × Δ precio implícito (la base de la planilla del back
     office, con su columna NO ENTRAN EN RxT); INTERMEDIACIÓN = el realizado
     la SUMATORIA de los boletos (compra negativa, venta positiva) =
     `ventas − compras`. El total es la SUMA de los dos (+ rentas).
  3. UN SOLO MOTOR: la fila del resumen y su modal salen de la misma pasada de
     `libro()`, así no pueden dar números distintos (REGLA #9).
"""
from __future__ import annotations

import pytest

from api.services.contabilidad_sql import calcular_titulos, ledger, libro, separar_altas

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
    assert f["no_entran_rxt"] == pytest.approx(-1_000_000)   # se fue 1M
    assert f["rxt"] == pytest.approx(1_000_000 * (0.60 - 0.50))
    assert f["intermediacion"] == pytest.approx(580_000)   # ventas − compras
    assert f["total"] == pytest.approx(100_000 + 580_000)
    assert f["cuadra"]  # qf − qi = −1M y la venta explica −1M


def test_baja_del_periodo_tiene_numero():
    """La celda roja de la planilla: se vendió todo → todo intermediación."""
    filas = _calc([_t("[100] AL30 - GD", 1_000_000, 500_000)], [],
                  [_b("venta", "AL30", 1_000_000, 540_000)])
    (f,) = filas
    assert f["estado"] == "baja"
    assert f["rxt"] == 0                                   # nada mantenido
    assert f["intermediacion"] == pytest.approx(540_000)   # ventas − compras
    assert f["total"] == pytest.approx(540_000)
    assert f["cuadra"]


def test_comprado_y_no_vendido_no_es_resultado():
    """LA REGLA (back office, 2026-09-01). Se compró a 600k y al cierre vale
    620k: esos 20k NO son resultado de este mes — no se realizó nada y no había
    posición al inicio. El modelo viejo los cantaba como intermediación."""
    filas = _calc([], [_t("[100] AL30 - GD", 1_000_000, 620_000)],
                  [_b("compra", "AL30", 1_000_000, -600_000)])
    (f,) = filas
    assert f["estado"] == "alta"
    assert f["rxt"] == 0                                    # no había nada al inicio
    assert f["intermediacion"] == pytest.approx(-600_000)   # la compra RESTA
    assert f["compras"] == pytest.approx(600_000)
    # La VALUACIÓN final (620.000) no aparece: no es resultado del mes.
    assert f["total"] == pytest.approx(-600_000)
    # Y como alta PURA no entra al informe, ese −600.000 no ensucia los totales.
    con_resultado, altas = separar_altas([f])
    assert con_resultado == [] and altas == [f]
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
    assert f["total"] == pytest.approx(-1_300_000)   # solo la compra, sin ventas


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
    calculados, no una identidad repartida."""
    filas = _calc(
        [_t("[100] AL30 - GD", 3_000_000, 1_500_000)],   # px 0,50
        [_t("[100] AL30 - GD", 2_500_000, 1_400_000)],   # px 0,56
        [_b("compra", "AL30", 500_000, -260_000),
         _b("venta", "AL30", 1_000_000, 545_000),
         _b("acreencia", "AL30", 0, 30_000)])
    (f,) = filas
    # Base del RxT = min(3M, 2,5M) = 2,5M: los nominales presentes todo el mes.
    assert f["no_entran_rxt"] == pytest.approx(-500_000)            # 2,5M − 3M
    assert f["rxt"] == pytest.approx(2_500_000 * (0.56 - 0.50))     # 150.000
    # Intermediación = sumatoria de boletos: ventas − compras.
    assert f["intermediacion"] == pytest.approx(545_000 - 260_000)  # 285.000
    assert f["rentas"] == pytest.approx(30_000)
    assert f["total"] == pytest.approx(150_000 + 285_000 + 30_000)  # 465.000
    assert f["rxt"] + f["intermediacion"] + f["rentas"] == pytest.approx(f["total"])
    assert f["cuadra"]  # −500k = +500k − 1M


def test_vender_todo_y_recomprar_mas_igual_tiene_tenencia():
    """Si hay nominales al INICIO y al CIERRE, SÍ O SÍ hay resultado por
    tenencia — y eso no impide que además haya intermediación (regla del back
    office). Se empieza con 1.000, se vende TODO y se recompran 1.200: del lote
    inicial no sobrevive nada, así que costear la tenencia por identidad de
    lotes daba **0** teniendo MÁS nominales al cierre que al principio.
    La base de la planilla, `min(1.000, 1.200)`, da los 1.000 correctos."""
    filas = _calc(
        [_t("[100] AL30 - GD", 1_000, 1_000)],    # px 1,00
        [_t("[100] AL30 - GD", 1_200, 1_320)],    # px 1,10
        [_b("venta", "AL30", 1_000, 1_080),
         _b("compra", "AL30", 1_200, -1_260)])
    (f,) = filas
    assert f["no_entran_rxt"] == pytest.approx(200)          # 1.200 − 1.000
    assert f["rxt"] == pytest.approx(1_000 * (1.10 - 1.00))  # 100 — NO cero
    assert f["intermediacion"] == pytest.approx(1_080 - 1_260)   # compró más de lo que vendió
    assert f["total"] == pytest.approx(100 - 180)
    assert f["cuadra"]


def test_cadena_rxt_contra_la_planilla_real():
    """La fila REAL de la planilla del back office (Toronto Trust Balanceado -
    Clase B, junio → julio). Congela los CINCO pasos, no solo el resultado:

        G no entran en RxT = E − C          H misma tenencia mantenida = min(C,E)
        I monto mes ant.   = H × (D/C)      J monto mes actual         = H × (F/E)
        K RxT              = J − I          L variación               = K / I
    """
    q = 61_481_010.022326
    (f,) = _calc([_t("[4135] CAFCI1389-4135", q, 241_799_955.418016)],
                 [_t("[4135] CAFCI1389-4135", q, 246_452_715.294486)], [],
                 u2m={}, m2d={})
    assert f["no_entran_rxt"] == 0
    assert f["tenencia_mantenida"] == pytest.approx(q)
    assert f["monto_rxt_ini"] == pytest.approx(241_799_955.418016, abs=0.01)
    assert f["monto_rxt_fin"] == pytest.approx(246_452_715.294486, abs=0.01)
    assert f["rxt"] == pytest.approx(4_652_759.8764696, abs=0.01)
    assert f["variacion_rxt"] == pytest.approx(0.019242, abs=1e-5)   # 1,92%


def test_cadena_rxt_planilla_real_con_nominales_distintos():
    """La fila que DESEMPATA (TX26, junio → julio). Acá los nominales cambian —
    suben de 19,8M a 34,2M — y `misma tenencia mantenida` copia **19.853.273**:
    el MENOR de los dos, no el mayor. Es lo que fija que H = min(C, E).

    Importa porque los 14.390.000 que se sumaron en el mes NO devengan
    tenencia: su resultado, si lo hay, es intermediación. Contarlos en las dos
    columnas sería contarlos dos veces."""
    (f,) = _calc([_t("[5925] TX26", 19_853_273.00, 139_985_427.923)],
                 [_t("[5925] TX26", 34_243_273.00, 246_209_132.87)], [],
                 u2m={}, m2d={})
    assert f["no_entran_rxt"] == pytest.approx(14_390_000.00)
    assert f["tenencia_mantenida"] == pytest.approx(19_853_273.00)   # el MENOR
    assert f["monto_rxt_ini"] == pytest.approx(139_985_427.923, abs=0.01)
    assert f["monto_rxt_fin"] == pytest.approx(142_745_032.87, abs=0.01)
    assert f["rxt"] == pytest.approx(2_759_604.947, abs=0.01)
    assert f["variacion_rxt"] == pytest.approx(0.0197, abs=1e-4)


def test_la_cadena_del_rxt_cierra_sola():
    """K = J − I y H sale de min(C,E), tambien cuando la posicion se mueve: el
    numero del informe no puede contradecir a sus propios pasos."""
    (f,) = _calc([_t("[100] AL30 - GD", 1_000, 1_000)],     # px 1,00
                 [_t("[100] AL30 - GD", 800, 880)], [],     # px 1,10
                 u2m={}, m2d={})
    assert f["no_entran_rxt"] == pytest.approx(-200)
    assert f["tenencia_mantenida"] == pytest.approx(800)
    assert f["monto_rxt_ini"] == pytest.approx(800 * 1.00)
    assert f["monto_rxt_fin"] == pytest.approx(800 * 1.10)
    assert f["rxt"] == pytest.approx(f["monto_rxt_fin"] - f["monto_rxt_ini"])


def test_no_entran_en_rxt_es_fin_menos_ini():
    """La columna de la planilla, literal: nominales del mes en curso menos
    nominales iniciales. Firmada — dice si la posición creció o se achicó."""
    (alta,) = _calc([], [_t("[100] AL30 - GD", 500, 500)], [])
    assert alta["no_entran_rxt"] == pytest.approx(500)
    (baja,) = _calc([_t("[100] AL30 - GD", 500, 500)], [], [])
    assert baja["no_entran_rxt"] == pytest.approx(-500)
    (quieto,) = _calc([_t("[100] AL30 - GD", 500, 500)],
                      [_t("[100] AL30 - GD", 500, 600)], [])
    assert quieto["no_entran_rxt"] == 0
    assert quieto["rxt"] == pytest.approx(100)   # todo el bloque devenga


def test_ao29_la_valuacion_nueva_no_es_ganancia():
    """REGRESIÓN del caso que rompió el módulo (cuenta propia, AO29, 08/26).

    Arrancó SIN el título y operó: compró por 1.788.643.523 y vendió por
    1.789.469.905 — neto **826.382**. Pero la foto del cierre trae 955.084 nominales valuados en 1.324.988.033 que NINGÚN boleto
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
    assert f["intermediacion"] == pytest.approx(826_381, abs=2)
    assert f["total"] == pytest.approx(826_381, abs=2)
    # La identidad vieja daba esto, y la valuación final NO puede aparecer:
    assert f["total"] != pytest.approx(1_325_814_415, abs=1)
    assert not f["cuadra"] and f["cuadre_nominales"] == pytest.approx(955_084)


def test_el_modal_y_la_fila_dan_lo_mismo():
    """REGLA #9: un solo motor. `libro()` es el que corre en las dos puntas, así
    que el `pnl_acum` de la última fila del modal == intermediación + rentas de
    la fila del resumen. Hasta 2026-09-01 eran cálculos distintos y para AO29
    daban números distintos sin que nada los comparara."""
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


def test_ledger_suma_los_importes_con_signo():
    """LA definición del back office: compra NEGATIVA, venta POSITIVA, ir
    sumando los importes. Los acumulados van fila por fila."""
    movs = [_mov("compra", 100, 1_000),
            _mov("compra", 100, 2_000),
            _mov("venta", 150, 2_400)]
    ag = ledger(movs)
    assert [m["nominales_acum"] for m in movs] == [100, 200, 50]
    assert [m["pnl_acum"] for m in movs] == [-1_000, -3_000, -600]
    assert ag["intermediacion"] == pytest.approx(2_400 - 3_000)
    assert ag["compras"] == pytest.approx(3_000)
    assert ag["ventas"] == pytest.approx(2_400)


def test_ledger_una_venta_sin_compra_suma_entera():
    """Vender algo que no se compró en el mes (venía de la posición inicial):
    la venta suma ENTERA. No hay costeo — la tenencia de esa posición la cobra
    la columna RxT, no ésta."""
    movs = [_mov("venta", 200, 3_000)]
    ag = ledger(movs)
    assert movs[-1]["pnl_acum"] == pytest.approx(3_000)
    assert movs[-1]["nominales_acum"] == pytest.approx(-200)
    assert ag["intermediacion"] == pytest.approx(3_000)


def test_ledger_la_posicion_inicial_mueve_nominales_pero_no_plata():
    """La fila sintética del cierre anterior NO es un boleto: aporta la
    posición de arranque y cero plata — si sumara su valuación, la
    intermediación arrancaría con un número que nadie operó."""
    movs = [_mov("saldo_inicial", 58_900, 93_745_240),
            _mov("venta", 58_900, 94_289_476)]
    ag = ledger(movs)
    assert movs[0]["pnl_acum"] == 0 and movs[0]["nominales_acum"] == 58_900
    assert movs[1]["pnl_acum"] == pytest.approx(94_289_476)
    assert movs[1]["nominales_acum"] == 0
    assert ag["intermediacion"] == pytest.approx(94_289_476)
    assert ag["compras"] == 0   # el saldo inicial NO es una compra


def test_ledger_rentas_y_otros():
    """La renta suma con signo al acumulado sin tocar nominales; un boleto sin
    dirección (caución/futuro) no toca nada pero muestra el acumulado vigente.
    Ojo: la renta entra al acumulado del MODAL, pero es su propio canal en el
    resumen — `intermediacion` son solo compras y ventas."""
    movs = [_mov("compra", 100, 1_000),
            _mov("acreencia", 0, 250),
            _mov(None, 999, 99_999)]
    ag = ledger(movs)
    assert movs[1]["nominales_acum"] == 100 and movs[1]["pnl_acum"] == -750
    assert movs[2]["nominales_acum"] == 100 and movs[2]["pnl_acum"] == -750
    assert ag["rentas"] == pytest.approx(250)
    assert ag["intermediacion"] == pytest.approx(-1_000)   # sin la renta


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
