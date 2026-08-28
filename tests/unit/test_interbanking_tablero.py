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
              "mayor": {}, "gastos": {},
              # ⚠️ **Los ajustes son POR FECHA, y eso no es un detalle del
              # mock.** Cuando era un solo dict para los dos días, el stub hacía
              # imposible distinguir «el ajuste entra en el cierre» de «entra
              # también en la apertura» — y el bug de 2026-08-27 (la apertura
              # sin manuales) pasó por acá en verde.
              "ajuste_previo": 0.0, "ajuste_hoy": 0.0}

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
    monkeypatch.setattr(bancos, "_mayor_del_dia", lambda f: estado["mayor"])

    def _saldos(f, cuenta_id=None):
        """Imita a `_saldos_banco`: el ajuste manual ya viene DENTRO del valor,
        el día que sea. Es el contrato que el tablero consume."""
        previo = f == PREVIO
        v = estado["previo"] if previo else estado["hoy"]
        if v is None:
            return {}
        ajuste = estado["ajuste_previo"] if previo else estado["ajuste_hoy"]
        return {CID: {"valor": round(v + ajuste, 2), "ajuste": ajuste,
                      "fuente": "extracto + ajuste manual" if ajuste else "extracto"}}

    monkeypatch.setattr(bancos, "_saldos_banco", _saldos)
    # ⚠️ La APERTURA se LEE del cierre sellado de ayer, no se recalcula. El stub
    # devuelve lo mismo que `_saldos_banco(PREVIO)` porque eso es justamente lo
    # que se sella: el sellado es una copia del cierre, no otro número.
    monkeypatch.setattr(bancos, "_cierre_sellado", lambda f: _saldos(f))

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
                ajuste_hoy=50_000.0)
    assert f["cierre_banco"] == 1_300_000.0
    assert f["ajuste_manual"] == 50_000.0
    assert f["diferencia"] == 0.0


def test_la_apertura_tambien_lleva_los_manuales_de_ayer(tablero):
    """El saldo inicial ES el cierre de ayer, así que trae los manuales de ayer
    igual que el cierre de hoy trae los de hoy: ayer cerró en 1.050.000
    (1.000.000 del banco + 50.000 cargados a mano), hoy el mayor movió +300.000 y
    el banco cerró en 1.350.000 → concilia.

    ⚠️ **Este test NO es el que agarra el bug de 2026-08-27**, y decir que sí
    sería peor que no tenerlo: el fixture mockea `_saldos_banco`, o sea que le da
    de comer la entrada YA arreglada. Pinea la aritmética de la composición. El
    guardarraíl del bug son los `test_saldos_banco_*` del final del archivo, que
    miran la función donde el ajuste realmente se perdía.
    """
    f = tablero(previo=1_000_000.0, ajuste_previo=50_000.0, hoy=1_350_000.0,
                mayor={CID: {"debe": 300_000.0, "haber": 0.0, "movimientos": 1}})
    assert f["saldo_inicio"] == 1_050_000.0
    assert f["saldo_final"] == 1_350_000.0
    assert f["diferencia"] == 0.0
    assert f["concilia"] is True


def test_el_manual_de_ayer_no_es_el_ajuste_que_se_publica(tablero):
    """`ajuste_manual` es el de HOY —cuánto del CIERRE lo puso una persona—; el de
    ayer ya está adentro del saldo inicial. Publicarlos mezclados haría que la
    pantalla explicara el cierre con un número del día anterior."""
    f = tablero(previo=1_000_000.0, ajuste_previo=50_000.0,
                hoy=1_000_000.0, ajuste_hoy=7_000.0, mayor={})
    assert f["saldo_inicio"] == 1_050_000.0
    assert f["ajuste_manual"] == 7_000.0


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


def test_una_cuenta_manual_dice_que_interbanking_no_la_informa(tablero):
    """No hay extracto ni saldo que esperar: culpar a «no llegó el extracto»
    manda al back office a buscar un archivo que no existe."""
    f = tablero(cuentas=[_cuenta(origen="manual")], previo=None, mayor={})
    assert f["saldo_inicio"] is None
    assert "Interbanking no informa" in f["motivo"]


# ── `_saldos_banco`: DONDE VIVÍA EL BUG ──────────────────────────────────────
# El fixture de arriba mockea `_saldos_banco`, así que prueba la ARITMÉTICA del
# tablero dando por buena su entrada. El bug de 2026-08-27 no estaba en la
# aritmética: estaba en que la entrada venía sin los manuales. Estos tests miran
# la función sola, que es la única forma de que eso no vuelva a pasar callado.

def _saldos(monkeypatch, fila, cuenta_id=None):
    """Corre `_saldos_banco` con UNA fila cruda. Devuelve `(resultado, sql, params)`."""
    visto = {}

    def _q(sql, params=None):
        visto["sql"], visto["params"] = sql, params
        return [{"cuenta_id": CID, "saldo_cierre": None, "informado": None,
                 "ajuste": 0, **fila}]

    monkeypatch.setattr(bancos, "_q", _q)
    out = bancos._saldos_banco(FECHA, cuenta_id)
    return out.get(CID), visto["sql"], visto["params"]


def test_saldos_banco_pide_los_manuales_del_dia(monkeypatch):
    """⚠️ **EL TEST QUE FALTABA.** La query tiene que traer los manuales de ESA
    fecha. Sin esto, la función devuelve el saldo pelado de Interbanking y el
    error queda invisible: cada consumidor parece correcto por su cuenta."""
    _, sql, params = _saldos(monkeypatch, {"saldo_cierre": 1_000_000})
    assert "bancos.movimientos_manuales" in sql
    assert "AS ajuste" in sql
    # El día PREVIO (para leer su cierre sellado) y después cinco veces la
    # fecha: extracto, saldos, movimientos del banco, manual del día y acumulado.
    assert params == (FECHA, FECHA, FECHA)


def test_saldos_banco_suma_el_ajuste_al_extracto(monkeypatch):
    s, _, _ = _saldos(monkeypatch, {"saldo_cierre": 1_000_000, "ajuste": 50_000})
    assert s["valor"] == 1_050_000.0
    assert s["ajuste"] == 50_000.0
    # La fuente lo CANTA: un saldo con plata puesta por una persona no se puede
    # mostrar igual que uno que informó el banco entero.
    assert s["fuente"] == "extracto"


def test_saldos_banco_suma_el_ajuste_tambien_al_saldo_informado(monkeypatch):
    """La cuenta QUIETA no tiene extracto (solo se emite con movimientos) y aun
    así puede tener un manual cargado encima."""
    s, _, _ = _saldos(monkeypatch, {"informado": 800_000, "ajuste": -25_000})
    assert s["valor"] == 775_000.0
    assert s["fuente"] == "saldo"


def test_saldos_banco_no_toca_la_fuente_sin_ajuste(monkeypatch):
    s, _, _ = _saldos(monkeypatch, {"saldo_cierre": 1_000_000})
    assert s["valor"] == 1_000_000.0
    assert s["fuente"] == "extracto"
    assert s["ajuste"] == 0.0


def test_saldos_banco_gana_el_extracto_sobre_el_informado(monkeypatch):
    """Precedencia intacta: el extracto es el cierre DECLARADO por el banco y
    viene con el detalle que lo explica. Las dos fuentes no se suman."""
    s, _, _ = _saldos(monkeypatch, {"saldo_cierre": 1_000_000, "informado": 999_999})
    assert s["valor"] == 1_000_000.0


def test_saldos_banco_la_cuenta_100_por_ciento_MANUAL_tiene_saldo(monkeypatch):
    """La cuenta que Interbanking no informa (Comafi, BNY, la Patagonia
    recaudadora): sin extracto ni saldo del banco, su saldo ES el acumulado de lo
    cargado a mano, arrancando de cero. Con el ajuste por día esto no se podía
    —lo del día es un movimiento, no un saldo—; acumulado sí lo es."""
    s, _, _ = _saldos(monkeypatch, {"ajuste": 50_000})
    assert s["valor"] == 50_000.0
    assert s["fuente"] == "manual"


def test_saldos_banco_sin_nada_de_nada_se_omite(monkeypatch):
    """Sin extracto, sin saldo y sin un solo manual no sabemos nada. Caer a cero
    fabricaría un descuadre del tamaño de la cuenta entera."""
    s, _, _ = _saldos(monkeypatch, {})
    assert s is None


def test_saldos_banco_por_cuenta_no_filtra_por_activa(monkeypatch):
    """El drill-down puede pedir una cuenta dada de baja que el tablero ya no
    lista; filtrarla la dejaría sin saldo y fabricaría un descuadre."""
    _, sql, params = _saldos(monkeypatch, {"saldo_cierre": 1.0}, cuenta_id=CID)
    assert "c.activa" not in sql
    assert params[-1] == CID
