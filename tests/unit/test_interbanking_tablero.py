"""El TABLERO de conciliación: `bancos.tablero`.

Se testea la aritmética de las columnas y —sobre todo— las dos decisiones que se
tomaron explícitamente y que, mal hechas, publican números falsos:

  · la DIFERENCIA compara contra el cierre del BANCO, no contra el saldo inicial;
  · `dif_sin_gastos` es `None` cuando no hay diferencia.
"""
from __future__ import annotations

from datetime import date

import pytest

from api.services import bancos

FECHA = date(2026, 8, 19)
PREVIO = date(2026, 8, 18)
CID = 7


def _cuenta(**kw):
    return {"id": CID, "bank_number": "034", "bank_name": "Patagonia",
            "account_number": "30410075359500020", "account_type": "CC",
            "currency": "ARS", "account_label": "PATAGONIA CC", "activa": True,
            "origen": "interbanking", "codigo_contable": "101010100002", **kw}


@pytest.fixture
def tablero(monkeypatch):
    """Arma el tablero con todo mockeado. Devuelve un `set(**kw)` que corre el
    caso y entrega la fila única."""
    estado = {"cuentas": [_cuenta()], "previo": 1_000_000.0, "hoy": 1_000_000.0,
              "mayor": {}, "gastos": {}, "ajustes": {}}

    def _q(sql, params=None):
        if "FROM bancos.cuentas" in sql:
            return estado["cuentas"]
        if "mayor_sync_log" in sql:
            return []
        return []

    monkeypatch.setattr(bancos, "_q", _q)
    monkeypatch.setattr(bancos, "restar_habiles", lambda f, n: PREVIO)
    monkeypatch.setattr(bancos, "_baldes", list)
    monkeypatch.setattr(bancos, "_gastos_bancarios", lambda f, b: estado["gastos"])
    monkeypatch.setattr(bancos, "_ajuste_manual", lambda f: estado["ajustes"])
    monkeypatch.setattr(bancos, "_mayor_del_dia", lambda f: estado["mayor"])

    def _saldos(f):
        v = estado["previo"] if f == PREVIO else estado["hoy"]
        return {} if v is None else {CID: {"valor": v, "fuente": "extracto"}}

    monkeypatch.setattr(bancos, "_saldos_banco", _saldos)

    def correr(**kw):
        estado.update(kw)
        return bancos.tablero("x@y.com", FECHA)["filas"][0]

    return correr


# ── la aritmética ────────────────────────────────────────────────────────────

def test_saldo_final_es_inicio_mas_debe_mas_haber(tablero):
    """`haber` viene NEGATIVO del mayor, así el saldo final es una suma."""
    f = tablero(previo=1_000_000.0,
                mayor={CID: {"debe": 500_000.0, "haber": -200_000.0, "movimientos": 5}})
    assert f["saldo_final"] == 1_300_000.0


def test_la_diferencia_compara_contra_el_cierre_del_banco(tablero):
    """⚠️ Si fuera `saldo_final − saldo_inicio` daría `debe + haber` y NUNCA
    miraría al banco: la columna no podría mostrar un descuadre ni queriendo."""
    f = tablero(previo=1_000_000.0, hoy=1_250_000.0,
                mayor={CID: {"debe": 300_000.0, "haber": 0.0, "movimientos": 2}})
    # mayor cerró en 1.300.000 y el banco en 1.250.000
    assert f["saldo_final"] == 1_300_000.0
    assert f["diferencia"] == -50_000.0
    assert f["diferencia"] != f["debe"] + f["haber"]


def test_el_signo_es_el_mismo_que_el_del_drill_down(tablero):
    """⚠️ La resta va **banco − mayor**, igual que `conciliar()`.

    Con el orden invertido el MISMO descuadre se vería `+50.000` en la grilla y
    `−50.000` al hacer click en la fila. Y es además el signo con el que ya
    están guardados los pendientes y el que decide `falta_en_el_mayor` /
    `sobra_en_el_mayor`.
    """
    f = tablero(previo=1_000_000.0, hoy=1_250_000.0,
                mayor={CID: {"debe": 300_000.0, "haber": 0.0, "movimientos": 2}})
    nuestro, excel = f["cierre_banco"], f["saldo_final"]
    assert f["diferencia"] == round(nuestro - excel, 2)


def test_sin_diferencia_concilia(tablero):
    f = tablero(previo=1_000_000.0, hoy=1_300_000.0,
                mayor={CID: {"debe": 300_000.0, "haber": 0.0, "movimientos": 2}})
    assert f["diferencia"] == 0.0
    assert f["concilia"] is True


def test_el_ajuste_manual_va_del_lado_del_banco(tablero):
    """Es plata que el banco no informó y alguien cargó a mano; sumarla del lado
    del mayor la contaría al revés."""
    f = tablero(previo=1_000_000.0, hoy=1_250_000.0,
                mayor={CID: {"debe": 300_000.0, "haber": 0.0, "movimientos": 1}},
                ajustes={CID: {"ajuste": 50_000.0}})
    assert f["cierre_banco"] == 1_300_000.0
    assert f["diferencia"] == 0.0


# ── dif_sin_gastos ───────────────────────────────────────────────────────────

def test_dif_sin_gastos_descuenta_el_gasto_todavia_no_cargado(tablero):
    """El banco cobró la comisión y el mayor todavía no la tiene: la diferencia
    ES el gasto, y al aplicarlo queda 0 = «no hay nada más»."""
    f = tablero(previo=1_000_000.0, hoy=999_000.0,
                mayor={CID: {"debe": 0.0, "haber": 0.0, "movimientos": 0}},
                gastos={CID: {"total": 1_000.0}})
    assert f["diferencia"] == -1_000.0
    assert f["dif_sin_gastos"] == 0.0


def test_dif_sin_gastos_es_none_cuando_ya_no_hay_diferencia(tablero):
    """⚠️ EL CASO QUE ROMPE. Una vez que el equipo carga el gasto en el mayor la
    diferencia se va a cero, pero `gastos` sigue valiendo lo mismo (sale de los
    movimientos del BANCO). Restarlo igual publicaría un `−1.000` inventado."""
    f = tablero(previo=1_000_000.0, hoy=999_000.0,
                mayor={CID: {"debe": 0.0, "haber": -1_000.0, "movimientos": 1}},
                gastos={CID: {"total": 1_000.0}})
    assert f["diferencia"] == 0.0
    assert f["dif_sin_gastos"] is None


def test_dif_sin_gastos_muestra_lo_que_sobra_ademas_del_gasto(tablero):
    """El caso que la columna existe para encontrar: hay gasto Y otra cosa."""
    f = tablero(previo=1_000_000.0, hoy=949_000.0,
                mayor={CID: {"debe": 0.0, "haber": 0.0, "movimientos": 0}},
                gastos={CID: {"total": 1_000.0}})
    assert f["diferencia"] == -51_000.0
    assert f["dif_sin_gastos"] == -50_000.0


def test_los_gastos_no_entran_en_ningun_total(tablero):
    """Son informativos. Si tocaran el saldo final, se contarían dos veces el día
    que el equipo los cargue en el mayor."""
    sin = tablero(previo=1_000_000.0, hoy=1_000_000.0, mayor={}, gastos={})
    con = tablero(previo=1_000_000.0, hoy=1_000_000.0, mayor={},
                  gastos={CID: {"total": 9_999.0}})
    assert sin["saldo_final"] == con["saldo_final"]
    assert sin["diferencia"] == con["diferencia"]


# ── faltantes: «no sé» no es «cero» ──────────────────────────────────────────

def test_sin_saldo_de_apertura_no_calcula_nada(tablero):
    """Feriado o extracto que no llegó. Caer a cero inventaría un descuadre del
    tamaño del saldo entero."""
    f = tablero(previo=None, mayor={CID: {"debe": 1.0, "haber": 0.0, "movimientos": 1}})
    assert f["saldo_inicio"] is None
    assert f["saldo_final"] is None
    assert f["diferencia"] is None
    assert "apertura" in f["motivo"]


def test_sin_cierre_del_banco_no_calcula_la_diferencia(tablero):
    f = tablero(previo=1_000_000.0, hoy=None, mayor={})
    assert f["saldo_final"] == 1_000_000.0
    assert f["diferencia"] is None
    assert f["motivo"]


def test_una_cuenta_sin_mapear_se_marca(tablero):
    """Sin `codigo_contable` no hay mayor: la fila tiene que decirlo, no mostrar
    debe/haber en cero como si el mayor estuviera vacío."""
    f = tablero(cuentas=[_cuenta(codigo_contable=None)], mayor={})
    assert f["tiene_mayor"] is False
