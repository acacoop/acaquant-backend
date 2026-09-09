"""Los números del Excel que pasó el user, clavados contra el service.

La calculadora de FINANCIAMIENTO reemplaza una planilla que la mesa ya usaba para
cotizar. El riesgo no es que el código explote — es que devuelva un número
PLAUSIBLE y distinto al de la planilla, y que nadie lo note hasta que un cliente
reclame. Por eso el caso de referencia es literal: los mismos inputs de la foto
(50.000.000 · 25 % · 127 días · aval 4 % · arancel 1 % · derecho 0,06 %) contra
los mismos seis resultados.

Si tocás una fórmula de `financiamiento_calc` y este archivo NO falla, no tocaste
lo que creías.
"""
from datetime import date

import pytest

from api.services.financiamiento_calc import calcular_lote_puro, calcular_puro

# Caso de la planilla. Los valores esperados salen de las celdas del Excel, no de
# correr el código y copiar la salida (eso testearía que el código hace lo que
# hace, que es siempre verdad).
CASO = dict(
    monto=50_000_000.0,
    tasa_pct=25.0,
    dias=127,
    arancel_aca_pct=1.00,
    derecho_mercado_pct=0.06,
    costo_aval_pct=4.00,
)


@pytest.fixture(scope="module")
def r():
    return calcular_puro(**CASO, hoy=date(2026, 8, 12))


@pytest.mark.parametrize(("campo", "esperado"), [
    ("monto_descontado",  45_998_739.76),
    ("arancel_aca",          173_972.60),
    ("derecho_mercado",       27_599.24),
    ("iva_derecho",            5_795.84),
    ("iva_aranceles",         36_534.25),
    ("a_recibir_cliente", 45_754_837.83),
])
def test_neto_sin_aval(r, campo, esperado):
    assert r["sin_aval"][campo] == pytest.approx(esperado, abs=0.01)


def test_tasa_directa(r):
    """8,00 % — el descuento efectivo del período, no anualizado."""
    assert r["sin_aval"]["tasa_directa_pct"] == pytest.approx(8.00, abs=0.01)


def test_neto_con_aval(r):
    assert r["con_aval"]["comision_sgr"] == pytest.approx(695_890.41, abs=0.01)
    assert r["con_aval"]["monto_descontado"] == pytest.approx(45_058_947.42, abs=0.01)
    # Tasa final = tasa pactada + costo del aval. Es la lectura comercial, NO el
    # costo real de la operación (ese es el CFT, que da 34,86 %).
    assert r["con_aval"]["tasa_final_pct"] == pytest.approx(29.00, abs=0.01)


def test_cft(r):
    assert r["cft_pct"] == pytest.approx(34.86, abs=0.01)


def test_flujos(r):
    """Entra el neto HOY, sale el nominal a los 127 días corridos (17/12/2026)."""
    assert r["flujos"] == [
        {"fecha": "2026-08-12", "importe": pytest.approx(45_058_947.42, abs=0.01)},
        {"fecha": "2026-12-17", "importe": pytest.approx(-50_000_000.0, abs=0.01)},
    ]


def test_sin_aval_elegido_no_calcula_el_bloque_de_abajo():
    """No haber elegido SGR todavía es un estado válido, no un error: el bloque
    SIN AVAL se devuelve igual y el resto viaja en null."""
    r = calcular_puro(**{**CASO, "costo_aval_pct": None})
    assert r["sin_aval"]["a_recibir_cliente"] == pytest.approx(45_754_837.83, abs=0.01)
    assert r["con_aval"] is None
    assert r["cft_pct"] is None
    assert r["flujos"] == []


@pytest.mark.parametrize(("kw", "msg"), [
    ({"dias": 0},   "dias"),
    ({"dias": -5},  "dias"),
    ({"monto": 0},  "monto"),
])
def test_parametros_imposibles_gritan(kw, msg):
    """Días 0 dividiría por cero en el CFT y monto 0 en la tasa directa. La
    calculadora NO puede degradar a un número: tiene que rechazar la entrada."""
    with pytest.raises(ValueError, match=msg):
        calcular_puro(**{**CASO, **kw})


def test_tabla_faltante_no_sale_como_error_generico(monkeypatch):
    """Guardar una SGR con el schema SIN aplicar tiene que decir QUÉ falta.

    Antes tiraba un HTTP 500 pelado: el usuario veía "HTTP 500" y no había forma
    de saber que lo único pendiente era correr `apply_schema` en el Droplet. El
    router traduce esta excepción a un 503 con el mensaje accionable.
    """
    from psycopg import errors as pg_errors

    from api.services import financiamiento_calc as fc

    def _explota(*_a, **_kw):
        raise pg_errors.UndefinedTable("relation does not exist")

    monkeypatch.setattr(fc, "get_pool", _explota)
    with pytest.raises(fc.TablasFaltantes, match="apply_schema"):
        fc.guardar_aval(nombre="Trend SGR", costo_cheque=5.0, costo_pagare=2.0,
                        nota=None, orden=0, actor="x@y.com")


def test_otros_errores_de_base_NO_se_disfrazan_de_schema_faltante(monkeypatch):
    """Un constraint violado no es un schema sin aplicar. Si se confundieran,
    el mensaje mandaría a correr `apply_schema` por un problema que eso no
    arregla — y el error real quedaría tapado."""
    from psycopg import errors as pg_errors

    from api.services import financiamiento_calc as fc

    def _explota(*_a, **_kw):
        raise pg_errors.CheckViolation("check constraint")

    monkeypatch.setattr(fc, "get_pool", _explota)
    with pytest.raises(pg_errors.CheckViolation):
        fc.guardar_aranceles(arancel_aca=1.0, derecho_mercado=0.06, actor="x@y.com")


def test_cft_null_si_el_aval_se_come_todo_el_neto():
    """Con parámetros absurdos el neto queda negativo y (M/D)^(365/d) sería un
    complejo. Se devuelve null — la pantalla muestra '—' en vez de un número
    inventado."""
    r = calcular_puro(**{**CASO, "costo_aval_pct": 400.0})
    assert r["con_aval"]["monto_descontado"] < 0
    assert r["cft_pct"] is None


# ──────────────────────────────────────────────────────────────────────────────
# LOTE — N cheques con el mismo instrumento y el mismo aval
#
# El lote de referencia es el que trajo el comercial en su propio Excel: mismo
# instrumento (CHEQUE), aval 4 %, arancel ACA 1 %, derecho de mercado 0,06 %.
# Los esperados de acá abajo salen de aplicar A MANO las fórmulas de
# `calcular_puro` — NO de correr el código y copiar la salida.
#
# El "Monto Bruto" de cada fila (4.821.664 / 2.988.741 / 7.169.163) y la
# comisión SGR total (116.384) SÍ coinciden con el Excel del comercial: son la
# misma cuenta. El arancel ACA y el derecho de mercado NO coinciden con ese
# Excel a propósito — esa planilla usa un piso de 0,25 % directo (base 360) y
# prorratea el derecho cada 90 días, fórmulas que este módulo NO adopta (ver
# el docstring del service).
# ──────────────────────────────────────────────────────────────────────────────

LOTE = [
    {"monto": 5_000_000.0, "tasa_pct": 45.0, "dias": 30},
    {"monto": 3_200_000.0, "tasa_pct": 43.0, "dias": 60},
    {"monto": 8_000_000.0, "tasa_pct": 47.0, "dias": 90},
]


@pytest.fixture(scope="module")
def rl():
    return calcular_lote_puro(
        items=LOTE, arancel_aca_pct=1.00, derecho_mercado_pct=0.06,
        costo_aval_pct=4.00, hoy=date(2026, 9, 9),
    )


@pytest.mark.parametrize(("i", "campo", "esperado"), [
    (0, "monto_descontado", 4_821_664.46),
    (0, "descuento",          178_335.54),
    (0, "arancel_aca",          4_109.59),
    (0, "derecho_mercado",      2_893.00),
    (0, "iva_derecho",            607.53),
    (0, "iva_aranceles",          863.01),
    (0, "a_recibir_cliente",4_813_191.33),
    (0, "comision_sgr",        16_438.36),
    (0, "neto_final",       4_796_752.98),
    (1, "monto_descontado", 2_988_741.04),
    (1, "neto_final",       2_959_165.19),
    (2, "monto_descontado", 7_169_162.78),
    (2, "neto_final",       7_061_185.36),
])
def test_lote_filas(rl, i, campo, esperado):
    assert rl["filas"][i][campo] == pytest.approx(esperado, abs=0.01)


def test_lote_fila_1_n_vencimiento_tasa_directa(rl):
    f = rl["filas"][0]
    assert f["n"] == 1
    assert f["vencimiento"] == "2026-10-09"
    assert f["tasa_directa_pct"] == pytest.approx(3.57, abs=0.01)


def test_lote_fila_2_vencimiento(rl):
    assert rl["filas"][1]["vencimiento"] == "2026-11-08"


def test_lote_fila_3_vencimiento(rl):
    assert rl["filas"][2]["vencimiento"] == "2026-12-08"


@pytest.mark.parametrize(("campo", "esperado"), [
    ("monto",                16_200_000.0),
    ("descuento",              1_220_431.71),
    ("monto_descontado",      14_979_568.29),
    ("arancel_aca",               29_095.89),
    ("derecho_mercado",            8_987.74),
    ("iva_derecho",                1_887.43),
    ("iva_aranceles",              6_110.14),
    ("a_recibir_cliente",     14_933_487.09),
    ("comision_sgr",             116_383.56),
    ("neto_final",            14_817_103.53),
])
def test_lote_totales(rl, campo, esperado):
    assert rl["totales"][campo] == pytest.approx(esperado, abs=0.01)


def test_lote_plazo_ponderado(rl):
    """Ponderado por NOMINAL (SUMPRODUCT(dias, monto) / SUM(monto)), como la
    planilla del comercial — 65,6 días."""
    assert rl["plazo_ponderado_dias"] == pytest.approx(65.5556, abs=0.001)


def test_lote_cft(rl):
    assert rl["cft_pct"] == pytest.approx(64.35, abs=0.01)


def test_lote_flujos(rl):
    """Entra el neto final total hoy; sale −monto en cada vencimiento distinto,
    ninguno de los tres cae el mismo día."""
    assert rl["flujos"] == [
        {"fecha": "2026-09-09", "importe": pytest.approx(14_817_103.53, abs=0.01)},
        {"fecha": "2026-10-09", "importe": pytest.approx(-5_000_000.0, abs=0.01)},
        {"fecha": "2026-11-08", "importe": pytest.approx(-3_200_000.0, abs=0.01)},
        {"fecha": "2026-12-08", "importe": pytest.approx(-8_000_000.0, abs=0.01)},
    ]


def test_lote_dos_cheques_mismo_dia_se_juntan_en_un_flujo():
    """Dos filas que vencen el mismo día no generan dos salidas de caja: el
    cliente paga los dos cheques juntos, así que el flujo también va junto."""
    items = [
        {"monto": 1_000_000.0, "tasa_pct": 40.0, "dias": 30},
        {"monto": 2_000_000.0, "tasa_pct": 40.0, "dias": 30},
    ]
    r = calcular_lote_puro(
        items=items, arancel_aca_pct=1.00, derecho_mercado_pct=0.06,
        costo_aval_pct=4.00, hoy=date(2026, 9, 9),
    )
    salidas = [f for f in r["flujos"] if f["fecha"] == "2026-10-09"]
    assert len(salidas) == 1
    assert salidas[0]["importe"] == pytest.approx(-3_000_000.0, abs=0.01)


def test_lote_sin_aval_cft_no_es_null(rl):
    """A diferencia del simulador SIMPLE, el CFT del lote se calcula SIEMPRE:
    sin SGR el neto final ES el a_recibir_cliente, y el reporte se manda al
    cliente igual en una operación directa sin aval."""
    r = calcular_lote_puro(
        items=LOTE, arancel_aca_pct=1.00, derecho_mercado_pct=0.06,
        costo_aval_pct=None, hoy=date(2026, 9, 9),
    )
    for f in r["filas"]:
        assert f["comision_sgr"] is None
        assert f["neto_final"] == pytest.approx(f["a_recibir_cliente"], abs=0.01)
    assert r["totales"]["comision_sgr"] is None
    assert r["totales"]["neto_final"] == pytest.approx(
        r["totales"]["a_recibir_cliente"], abs=0.01
    )
    assert r["totales"]["neto_final"] == pytest.approx(14_933_487.09, abs=0.01)
    assert r["cft_pct"] == pytest.approx(57.34, abs=0.01)


def test_lote_vacio_grita():
    with pytest.raises(ValueError, match="no tiene filas"):
        calcular_lote_puro(
            items=[], arancel_aca_pct=1.0, derecho_mercado_pct=0.06, costo_aval_pct=4.0,
        )


def test_lote_mas_de_50_filas_grita():
    items = [{"monto": 1_000.0, "tasa_pct": 10.0, "dias": 30}] * 51
    with pytest.raises(ValueError, match="50"):
        calcular_lote_puro(
            items=items, arancel_aca_pct=1.0, derecho_mercado_pct=0.06, costo_aval_pct=4.0,
        )


def test_calcular_lote_entrada(monkeypatch):
    """La entrada del endpoint: valida por fila (con el número de fila en el
    mensaje) y resuelve el aval UNA vez contra el catálogo mockeado."""
    from api.services import financiamiento_calc as fc

    monkeypatch.setattr(fc, "get_datos", lambda: {
        "avales": [{"nombre": "Conaval", "costo_cheque": 4.0, "costo_pagare": 3.0,
                    "nota": ""}],
        "aranceles": {"arancel_aca": 1.0, "derecho_mercado": 0.06},
        "iva_pct": fc.IVA_PCT, "base_anual": fc.BASE_ANUAL, "disponible": True,
    })

    with pytest.raises(ValueError, match="fila 2"):
        fc.calcular_lote(
            items=[
                {"monto": 5_000_000, "tasa_pct": 45, "dias": 30},
                {"monto": 0, "tasa_pct": 45, "dias": 30},
            ],
            aval="Conaval", instrumento="cheque",
        )

    res = fc.calcular_lote(items=LOTE, aval="Conaval", instrumento="cheque")
    assert res["params"]["costo_aval_pct"] == 4.0
    assert res["params"]["aval"] == "Conaval"
    assert isinstance(res["params"]["hoy"], str)
    date.fromisoformat(res["params"]["hoy"])  # no tira: es una fecha ISO válida
    assert len(res["filas"]) == 3
