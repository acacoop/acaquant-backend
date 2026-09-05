"""Tests del cálculo puro de CONTABILIDAD (api/services/contabilidad_sql.py).

LA VALUACIÓN DEPENDE DE LA SITUACIÓN — no hay una sola fórmula. El invariante
que las une es que **TENENCIA + INTERMEDIACIÓN == LA PLATA REAL DEL MES**
(`v_fin − v_ini + ventas − compras`) en todos los casos donde
los nominales cuadran. Ocho casos, de simple a complejo: quieta · intradía ·
alta · baja · achicó · agrandó · rotó · vendió-todo-y-recompró, y los recorre
`test_los_ocho_casos_dan_la_plata_real`.

  · TENENCIA = lo MANTENIDO, `min(N₀,N₁) × Δ precio implícito` (la cadena de la
    planilla del back office, con su columna NO ENTRAN EN RxT), MÁS —si la
    posición creció— el valor al cierre de lo NUEVO menos lo que costó.
  · INTERMEDIACIÓN = `ventas − compras`, MENOS el activo que SALIÓ (a su precio
    del cierre anterior) y SIN el costo de lo que quedó en cartera (ese se lo
    llevó la tenencia).
  · Lo que se reclasifica está topeado por lo que los boletos explican EN NETO:
    ante un descuadre no se valúa, se marca.
  · UN SOLO MOTOR: la fila y su modal salen de la misma pasada de `libro()`
    (REGLA #9); el modal muestra la caja corrida y la fila el ajuste, con el
    puente explícito en `test_el_modal_y_la_fila_dan_lo_mismo`.
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
    # La venta entra por 580.000 pero el activo que salió también sale:
    # 1.000.000 × 0,50 (precio del cierre anterior) = 500.000.
    assert f["intermediacion"] == pytest.approx(580_000 - 500_000)
    assert f["total"] == pytest.approx(180_000)   # = la plata real
    assert f["cuadra"]  # qf − qi = −1M y la venta explica −1M


def test_baja_del_periodo_tiene_numero():
    """La celda roja de la planilla: se vendió todo → todo intermediación."""
    filas = _calc([_t("[100] AL30 - GD", 1_000_000, 500_000)], [],
                  [_b("venta", "AL30", 1_000_000, 540_000)])
    (f,) = filas
    assert f["estado"] == "baja"
    assert f["rxt"] == 0                                   # nada mantenido
    # Vender lo que YA se tenía no es ganancia entera: se costea contra su
    # valuación del cierre anterior (500.000).
    assert f["intermediacion"] == pytest.approx(40_000)
    assert f["total"] == pytest.approx(40_000)
    assert f["cuadra"]


def test_comprado_y_retenido_es_resultado_por_tenencia():
    """Se compró a 600k y al cierre vale 620k. Esos 20k SÍ son resultado, y son
    de TENENCIA (regla del back office): el valor al cierre de lo nuevo menos lo
    que costó. La valuación del cierre es la foto final y no se toca — ya lo
    tiene todo implícito. La compra NO resta en intermediación: su costo se lo
    lleva la tenencia."""
    filas = _calc([], [_t("[100] AL30 - GD", 1_000_000, 620_000)],
                  [_b("compra", "AL30", 1_000_000, -600_000)])
    (f,) = filas
    assert f["estado"] == "alta"
    assert f["rxt"] == pytest.approx(20_000)     # 620.000 al cierre − 600.000 de costo
    assert f["rxt_nueva"] == pytest.approx(20_000)
    assert f["intermediacion"] == 0              # la compra NO resta acá
    assert f["compras"] == pytest.approx(600_000)
    assert f["total"] == pytest.approx(20_000)   # = la plata real
    assert f["cuadra"]


def test_las_rentas_no_existen_en_el_informe():
    """NO HAY canal de rentas (regla del user, 2026-09-02). Un cupón cobrado no
    entra por ningún lado: ni columna, ni suma al total, y la tabla de donde
    salían (`negocio_movimientos`) ya no se lee."""
    filas = _calc([_t("[100] AL30 - GD", 1_000_000, 500_000)],
                  [_t("[100] AL30 - GD", 1_000_000, 510_000)],
                  [_b("acreencia", "AL30", 0, 25_000)])
    (f,) = filas
    assert "rentas" not in f
    assert f["rxt"] == pytest.approx(10_000)
    assert f["intermediacion"] == pytest.approx(0)
    assert f["total"] == pytest.approx(10_000)   # el cupón NO suma


def test_cuadre_detecta_boleto_faltante():
    """Los nominales bajaron y ningún boleto lo explica → la fila no cuadra."""
    filas = _calc([_t("[100] AL30 - GD", 2_000_000, 1_000_000)],
                  [_t("[100] AL30 - GD", 1_500_000, 750_000)], [])
    (f,) = filas
    assert not f["cuadra"]
    assert f["cuadre_nominales"] == pytest.approx(-500_000)


def test_boleto_usd_pesifica_con_su_mep():
    """La compra en USD se pesifica con el mep del boleto: 1.300.000. Al cierre
    vale 1.400.000 → 100.000 de resultado por tenencia."""
    filas = _calc([], [_t("[100] AL30 - GD", 1_000, 1_400_000)],
                  [_b("compra", "AL30", 1_000, -1_000, moneda="USD", mep=1_300.0)])
    (f,) = filas
    assert f["compras"] == pytest.approx(1_300_000)
    assert f["total"] == pytest.approx(100_000)   # 1.400.000 al cierre − 1.300.000


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
    # Se fueron 500k nominales (lo que las ventas explican en NETO), a su
    # precio del cierre anterior: 500.000 × 0,50 = 250.000.
    assert f["intermediacion"] == pytest.approx(545_000 - 260_000 - 250_000)  # 35.000
    assert f["total"] == pytest.approx(185_000)   # el cupón de 30.000 NO cuenta
    assert f["rxt"] + f["intermediacion"] == pytest.approx(f["total"])
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
    # Tenencia = lo mantenido (1.000 × 0,10 = 100) + lo nuevo que quedó
    # (200 al cierre 1,10 = 220, menos su costo 200 × 1,05 = 210) = 110.
    assert f["rxt_mantenida"] == pytest.approx(100)          # NO cero
    assert f["rxt_nueva"] == pytest.approx(10)
    assert f["rxt"] == pytest.approx(110)
    assert f["intermediacion"] == pytest.approx(30)
    assert f["total"] == pytest.approx(140)                  # = la plata real
    assert f["cuadra"]


def test_los_ocho_casos_dan_la_plata_real():
    """EL INVARIANTE del módulo, sobre los ocho casos que se pueden dar.

    No hay UNA fórmula: la valuación depende de la situación. Lo que las une es
    que, cuando los nominales cuadran, TENENCIA + INTERMEDIACIÓN tiene
    que dar exactamente la plata del mes — `v_fin − v_ini + ventas − compras`.
    Cada vez que este test se rompió, la pantalla estaba inflando: AO29 mostró
    1.869.729 donde había −348.892, y una venta SENEBI llegó a mostrar 6.465
    millones de PnL por vender algo que ya se tenía.
    """
    casos = [
        # (nombre,               N0,     V0,    N1,     V1,   boletos)
        ("quieta",             1000,  1_000, 1000,  1_100, []),
        ("intradía",              0,      0,    0,      0,
         [_b("compra", "AL30", 500, -500), _b("venta", "AL30", 500, 540)]),
        ("alta",                  0,      0, 1000, 620_000,
         [_b("compra", "AL30", 1000, -600_000)]),
        ("baja",               1000,  1_000,    0,      0,
         [_b("venta", "AL30", 1000, 1_080)]),
        ("achicó",             1000,  1_000,  600,    660,
         [_b("venta", "AL30", 400, 440)]),
        ("agrandó",            1000,  1_000, 1500,  1_650,
         [_b("compra", "AL30", 500, -520)]),
        ("rotó",               1000,  1_000, 1000,  1_100,
         [_b("venta", "AL30", 400, 432), _b("compra", "AL30", 400, -420)]),
        ("vendió todo y +",    1000,  1_000, 1200,  1_320,
         [_b("venta", "AL30", 1000, 1_080), _b("compra", "AL30", 1200, -1_260)]),
    ]
    for nombre, n0, v0, n1, v1, bol in casos:
        (f,) = _calc([_t("[100] AL30 - GD", n0, v0)] if n0 or v0 else [],
                     [_t("[100] AL30 - GD", n1, v1)] if n1 or v1 else [], bol)
        real = v1 - v0 + f["ventas"] - f["compras"]
        assert f["cuadra"], nombre
        assert f["rxt"] + f["intermediacion"] == pytest.approx(f["total"]), nombre
        assert f["total"] == pytest.approx(real, abs=0.01), (
            f"{nombre}: total {f['total']} != plata real {real}")


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
    # Los 955.084 del cierre no los explica ningún boleto (compró y vendió la
    # MISMA cantidad, neto CERO) → no se acreditan como tenencia nueva.
    # Valuarlos devolvía los 1.325 millones de la fórmula vieja.
    assert f["qty_entraron"] == 0 and f["rxt_nueva"] == 0
    # La identidad vieja daba esto, y la valuación final NO puede aparecer:
    assert f["total"] != pytest.approx(1_325_814_415, abs=1)
    assert not f["cuadra"] and f["cuadre_nominales"] == pytest.approx(955_084)


def test_el_modal_y_la_fila_dan_lo_mismo():
    """REGLA #9: un solo motor. `libro()` es el que corre en las dos puntas, así
    que el `pnl_acum` de la última fila del modal se reconcilia con la
    intermediación de la fila del resumen. Hasta 2026-09-01 eran cálculos distintos y para AO29
    daban números distintos sin que nada los comparara."""
    bol = [_b("compra", "AL30", 500_000, -260_000),
           _b("venta", "AL30", 1_000_000, 545_000)]
    (f,) = _calc([_t("[100] AL30 - GD", 3_000_000, 1_500_000)],
                 [_t("[100] AL30 - GD", 2_500_000, 1_400_000)], bol)
    filas, _ = libro(key="AL30", qty_ini=3_000_000, v_ini=1_500_000,
                     fecha_ini="2026-07-31", boletos=bol)
    assert filas[0]["categoria"] == "saldo_inicial"      # la posición inicial primero
    # El modal muestra la CAJA corrida de los boletos (ventas − compras +
    # La fila ajusta esa caja por el activo que entró y el que salió;
    # el puente entre los dos números es explícito — si no cerrara, la fila y su
    # detalle se estarían contradiciendo (REGLA #9).
    puente = f["intermediacion"] + f["costo_salida"] - f["costo_nuevo"]
    assert filas[-1]["pnl_acum"] == pytest.approx(puente)


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


def test_ledger_ignora_lo_que_no_es_compra_ni_venta():
    """Una acreencia (cupón) y un boleto sin dirección (caución/futuro) NO tocan
    nada: ni nominales ni plata. Se ven en el modal con el acumulado vigente al
    lado —para que se sepa que estuvieron— pero no mueven el número."""
    movs = [_mov("compra", 100, 1_000),
            _mov("acreencia", 0, 250),
            _mov(None, 999, 99_999)]
    ag = ledger(movs)
    assert movs[1]["nominales_acum"] == 100 and movs[1]["pnl_acum"] == -1_000
    assert movs[2]["nominales_acum"] == 100 and movs[2]["pnl_acum"] == -1_000
    assert "rentas" not in ag
    assert ag["intermediacion"] == pytest.approx(-1_000)


def test_mes_contable_por_liquidacion():
    """Detección del user 2026-09-01: la tenencia es una foto LIQUIDADA y los
    boletos van por CONCERTACIÓN. El boleto pertenece al mes en que LIQUIDA:
    concertación + plazo real de `condiciones`, en hábiles."""
    from api.services.contabilidad_sql import pertenece_al_mes
    kw = {"mes": "2026-08"}
    # borde del mes anterior (31/07 viernes → 24hs liquida lunes 03/08)
    assert pertenece_al_mes("2026-07-31", "24hs", **kw)
    assert not pertenece_al_mes("2026-07-31", "Contado Inmediato", **kw)
    assert not pertenece_al_mes("2026-07-30", "24hs", **kw)      # liquida 31/7 → julio
    assert pertenece_al_mes("2026-07-29", "3 días", **kw)        # liquida 03/08 → agosto
    # adentro del mes
    assert pertenece_al_mes("2026-08-14", "24hs", **kw)
    assert pertenece_al_mes("2026-08-14", "Contado Inmediato", **kw)
    # borde de este mes
    assert not pertenece_al_mes("2026-08-31", "24hs", **kw)      # liquida 1/9 → septiembre
    assert pertenece_al_mes("2026-08-31", "Contado Inmediato", **kw)
    assert not pertenece_al_mes("2026-08-28", "48hs", **kw)      # liquida 01/09


def test_plazo_habiles_lee_condiciones():
    """Hasta 2026-09-04 solo se entendía «24»; medido: ~7% de los boletos
    propios traen 1/3/4/5/7 días o 48hs y caían como contado inmediato."""
    from api.services.contabilidad_sql import plazo_habiles
    assert plazo_habiles("24hs") == 1
    assert plazo_habiles("ARS 24hs") == 1
    assert plazo_habiles("48hs") == 2
    assert plazo_habiles("72 hs") == 3
    assert plazo_habiles("3 días") == 3
    assert plazo_habiles("7 dias") == 7
    assert plazo_habiles("1 día") == 1
    assert plazo_habiles("Contado Inmediato") == 0
    assert plazo_habiles("ARS Inm") == 0
    assert plazo_habiles(None) == 0


def test_orden_por_impacto():
    filas = _calc(
        [_t("[100] AL30 - GD", 1_000, 1_000), _t("[200] FCI X", 100, 1_000, cartera="FCI")],
        [_t("[100] AL30 - GD", 1_000, 1_010), _t("[200] FCI X", 100, 2_000, cartera="FCI")],
        [])
    assert [f["titulo"] for f in filas] == ["FCI X", "AL30"]


def _fci(fecha, tipo, cant, boleto, bruto=1_000.0, instrumento="TT AHORRO B",
         operacion="Rescate"):
    return {"fecha": fecha, "instrumento": instrumento, "operacion": operacion,
            "tipo_operacion": tipo, "condiciones": "ARS Inm", "cantidad": cant,
            "bruto": bruto, "moneda": "ARS", "mep": None, "boleto": boleto}


def test_sin_conciliar_no_suma_al_total():
    """La TENENCIA manda (user, 2026-09-04): una fila cuyos boletos no
    explican los nominales del cierre es PARTIDA SIN CONCILIAR — se muestra
    aparte y no entra al total del mes."""
    from api.services.contabilidad_sql import separar
    ok = {"cuadra": True, "estado": "operado", "ventas": 1.0, "total": 10.0}
    alta = {"cuadra": True, "estado": "alta", "ventas": 0.0, "total": 5.0}
    rota = {"cuadra": False, "estado": "alta", "ventas": 0.0, "total": -252.0}
    con, altas, sin = separar([ok, alta, rota])
    assert con == [ok] and altas == [alta] and sin == [rota]




# ── LA FUENTE NUEVA: movimientos_propias (2026-09-05) ────────────────────────
#
# Tres cosas cambiaron y las tres pueden fallar EN SILENCIO:
#   · los AJUSTES administrativos (mueven cantidad, no llevan plata) — son lo que
#     la fuente anterior no traía y por lo que el cuadre fallaba;
#   · los EXCLUIDOS a mano — sacan la plata y NO el hecho, si sacaran las dos
#     cosas tildar una casilla borraría el título entero del informe;
#   · la clasificación de una línea de título, que decide las dos anteriores.

def test_un_ajuste_mueve_nominales_y_no_plata():
    """Un canje/amortización cambia la posición sin importe. Si sumara plata,
    inventaría resultado; si no moviera nominales, el título descuadraría."""
    ag = ledger([_mov("ajuste", -300.0, None)])
    assert ag["intermediacion"] == 0.0
    assert ag["qty_ajustes"] == -300.0 and ag["n_ajustes"] == 1
    assert ag["compras"] == 0.0 and ag["ventas"] == 0.0


def test_el_ajuste_no_ensucia_el_precio_de_compra():
    """Si un ajuste entrara como compra de importe 0, `px_compra = compras /
    qty_compras` se diluiría y el costo de lo que quedó en cartera saldría mal."""
    ag = ledger([_mov("compra", 100.0, 1_000.0), _mov("ajuste", 900.0, None)])
    assert ag["qty_compras"] == 100.0          # el ajuste NO entra acá
    assert ag["compras"] / ag["qty_compras"] == 10.0


def test_el_ajuste_hace_cuadrar_lo_que_antes_no_cuadraba():
    """EL motivo del cambio de fuente. Una amortización baja 300 nominales sin
    boleto de venta: con la fuente vieja la fila descuadraba y se iba a «sin
    conciliar»; ahora cuadra y suma al mes."""
    t = _calc([_t("[100] AL30 - GD", 1000, 100_000)],
              [_t("[100] AL30 - GD", 700, 77_000)],
              [_b("ajuste", "AL30", -300.0, None)])[0]
    assert t["cuadra"] is True
    assert t["cuadre_nominales"] == 0


def test_sin_el_ajuste_la_fila_no_cuadra():
    """La contracara del anterior: es exactamente lo que pasaba antes."""
    t = _calc([_t("[100] AL30 - GD", 1000, 100_000)],
              [_t("[100] AL30 - GD", 700, 77_000)], [])[0]
    assert t["cuadra"] is False


def test_excluir_saca_la_plata_pero_no_el_hecho():
    """La regla del módulo. La venta excluida no suma un peso PERO sus nominales
    siguen moviéndose: si no, el título descuadraría por tildar una casilla."""
    b = _mov("venta", 300.0, 33_000.0)
    b["excluido"] = True
    ag = ledger([b])
    assert ag["intermediacion"] == 0.0        # la plata NO entró
    assert ag["qty_ajustes"] == -300.0        # el hecho SÍ movió la posición
    assert ag["excluidos"] == 1


def test_excluir_no_manda_la_fila_a_sin_conciliar():
    """El bug que la regla evita: excluir un movimiento no puede borrar el
    título entero del informe."""
    b = _b("venta", "AL30", 300.0, 33_000.0)
    b["excluido"] = True
    t = _calc([_t("[100] AL30 - GD", 1000, 100_000)],
              [_t("[100] AL30 - GD", 700, 77_000)], [b])[0]
    assert t["cuadra"] is True
    assert t["excluidos"] == 1
    assert t["intermediacion"] == 0.0


def test_el_excluido_se_declara_no_desaparece():
    """Un total que cambió porque alguien tildó una casilla tiene que poder
    explicarse desde la fila, sin abrir el modal."""
    b = _b("venta", "AL30", 300.0, 33_000.0)
    b["excluido"] = True
    t = _calc([_t("[100] AL30 - GD", 1000, 100_000)],
              [_t("[100] AL30 - GD", 700, 77_000)], [b])[0]
    assert t["excluido_total"] == 33_000.0


def test_clasificar_respeta_la_categoria_del_feed():
    from api.services.contabilidad_sql import _clasificar
    assert _clasificar("compra", 100, 1000) == "compra"
    assert _clasificar("venta", 100, 1000) == "venta"
    assert _clasificar("suscripcion_fci", 100, 1000) == "suscripcion_fci"


def test_clasificar_sin_cantidad_no_mueve_nada():
    from api.services.contabilidad_sql import _clasificar
    assert _clasificar("otro", None, 5000) is None
    assert _clasificar("comision", 0, 5000) is None


def test_clasificar_sin_plata_es_ajuste():
    """Lo que el user declaró VÁLIDO: mueve cantidad, no lleva precio ni importe."""
    from api.services.contabilidad_sql import _clasificar
    assert _clasificar("otro", -300, None) == "ajuste"
    assert _clasificar("", 500, 0) == "ajuste"


def test_clasificar_trd_se_decide_por_el_signo():
    """Un TRD no dice «Compra» ni «Venta» en el texto. Con el importe en signo
    cliente: negativo = pagamos = compra. Misma regla que `agrupar_boletos` —
    sin esto, un TRD cae en `ignorados` (el bug de YFCOO en el motor de PnL)."""
    from api.services.contabilidad_sql import _clasificar
    assert _clasificar("otro", 100, -71_500) == "compra"
    assert _clasificar("otro", 100, 71_500) == "venta"


def test_una_acreencia_que_mueve_nominales_es_ajuste_no_renta():
    """Una amortización baja nominales de verdad y la foto lo va a mostrar: sus
    NOMINALES tienen que contar para el cuadre y su PLATA no puede entrar al
    informe. Eso es exactamente un ajuste — y es la regla «no hay rentas»."""
    from api.services.contabilidad_sql import _clasificar
    assert _clasificar("acreencia", -300, 12_000) == "ajuste"


def test_la_clave_cae_al_ticker_si_el_catalogo_no_conoce_la_unidad():
    """El catálogo manda, pero una unidad que todavía no está en `assets` (un
    rebautizo, un alta reciente) cae a su ticker — el MISMO espacio de claves que
    usa el motor de PnL. Sin el fallback, esa fila queda huérfana y el mes da de
    menos con la pantalla en verde."""
    from api.services.contabilidad_sql import _clave
    assert _clave("[100] AL30 - GD", "XXX", _U2M) == "AL30"      # gana el catálogo
    assert _clave("[999] NUEVO", "NUEVO", _U2M) == "NUEVO"       # cae al ticker
    assert _clave("[999] SIN NADA", None, _U2M) == "[999] SIN NADA"
