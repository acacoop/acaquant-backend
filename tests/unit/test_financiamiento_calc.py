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

from api.services.financiamiento_calc import calcular_puro

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
