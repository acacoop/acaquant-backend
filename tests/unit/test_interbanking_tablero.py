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


def test_sin_saldo_no_se_culpa_al_extracto_del_banco(tablero):
    """El motivo dice «no hay saldo», a secas. Decía «no hay saldo DEL BANCO», y
    desde que el acumulado manual es un saldo válido por sí solo eso mandaba al
    back office a buscar un extracto que en una cuenta manual no existe."""
    f = tablero(previo=None, mayor={})
    assert f["saldo_inicio"] is None
    assert "no hay saldo al" in f["motivo"]


# ── `_saldos_banco`: DONDE VIVÍA EL BUG ──────────────────────────────────────
# El fixture de arriba mockea `_saldos_banco`, así que prueba la ARITMÉTICA del
# tablero dando por buena su entrada. El bug de 2026-08-27 no estaba en la
# aritmética: estaba en que la entrada venía sin los manuales. Estos tests miran
# la función sola, que es la única forma de que eso no vuelva a pasar callado.

def _saldos(monkeypatch, fila, manual=None, cuenta_id=None):
    """Corre `_saldos_banco` con UNA fila cruda de saldo y una de `_manuales`.
    Devuelve `(resultado, sqls, params)` — las DOS queries que hace."""
    visto = []

    def _q(sql, params=None):
        t = " ".join(str(sql).split())
        visto.append((t, params))
        if "movimientos_manuales" in t:
            if manual is None:
                return []
            return [{"cuenta_id": CID, "acumulado": 0, "del_dia": 0,
                     "movimientos": 1, "movs_dia": 1, **manual}]
        return [{"cuenta_id": CID, "saldo_cierre": None, "informado": None, **fila}]

    monkeypatch.setattr(bancos, "_q", _q)
    out = bancos._saldos_banco(FECHA, cuenta_id)
    return out.get(CID), [v[0] for v in visto], [v[1] for v in visto]


def _sql_manuales(sqls, params):
    """La query de los manuales y sus params, sea cual sea el orden."""
    return next((s, p) for s, p in zip(sqls, params) if "movimientos_manuales" in s)


def test_saldos_banco_pide_los_manuales_ACUMULADOS(monkeypatch):
    """⚠️⚠️ **EL BUG DE 2026-08-27, y es el corazón del asunto.** El banco no va a
    informar NUNCA un movimiento manual, así que su efecto sobre el saldo no dura
    un día: dura para siempre. La query tiene que pedir `fecha <= hoy`, no
    `fecha = hoy`.

    Con `=`, el saldo sale perfecto el día de la carga y al día siguiente vuelve
    al crudo de Interbanking — exactamente lo que reportó el back office.
    """
    _, sqls, params = _saldos(monkeypatch, {"saldo_cierre": 1_000_000})
    sql, prm = _sql_manuales(sqls, params)
    assert "fecha <= %s" in sql, "acumulado, no del día"
    assert prm == (FECHA, FECHA, FECHA)


def test_saldos_banco_suma_el_acumulado_no_lo_del_dia(monkeypatch):
    """El caso que reportó el back office: HOY no se cargó nada a mano, pero el
    manual de un día anterior sigue siendo parte del saldo."""
    s, _, _ = _saldos(monkeypatch, {"saldo_cierre": 1_000_000},
                      manual={"acumulado": 50_000, "del_dia": 0, "movs_dia": 0})
    assert s["valor"] == 1_050_000.0, "el manual de ayer NO se evapora"
    assert s["ajuste"] == 50_000.0
    assert s["ajuste_dia"] == 0.0


def test_saldos_banco_suma_el_ajuste_al_extracto(monkeypatch):
    s, _, _ = _saldos(monkeypatch, {"saldo_cierre": 1_000_000},
                      manual={"acumulado": 50_000, "del_dia": 50_000})
    assert s["valor"] == 1_050_000.0
    assert s["ajuste"] == 50_000.0
    # La fuente lo CANTA: un saldo con plata puesta por una persona no se puede
    # mostrar igual que uno que informó el banco entero.
    assert s["fuente"] == "extracto + ajuste manual"


def test_saldos_banco_suma_el_ajuste_tambien_al_saldo_informado(monkeypatch):
    """La cuenta QUIETA no tiene extracto (solo se emite con movimientos) y aun
    así puede tener un manual cargado encima."""
    s, _, _ = _saldos(monkeypatch, {"informado": 800_000},
                      manual={"acumulado": -25_000})
    assert s["valor"] == 775_000.0
    assert s["fuente"] == "saldo informado por el banco + ajuste manual"


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


def test_saldos_banco_la_cuenta_100_por_ciento_MANUAL_arranca_de_cero(monkeypatch):
    """El banco que no está en Interbanking: sin extracto ni saldo, su saldo ES
    el acumulado de lo cargado a mano. Con el ajuste POR DÍA esto no se podía
    hacer —lo del día es un movimiento, no un saldo—; acumulado sí lo es."""
    s, _, _ = _saldos(monkeypatch, {}, manual={"acumulado": 50_000, "movimientos": 3})
    assert s["valor"] == 50_000.0
    assert s["fuente"] == "manual"


def test_saldos_banco_sin_nada_de_nada_se_omite(monkeypatch):
    """«No sabemos» no es «cero»: caer a cero fabricaría un descuadre del tamaño
    de la cuenta entera."""
    s, _, _ = _saldos(monkeypatch, {})
    assert s is None


def test_saldos_banco_por_cuenta_no_filtra_por_activa(monkeypatch):
    """El drill-down puede pedir una cuenta dada de baja que el tablero ya no
    lista; filtrarla la dejaría sin saldo y fabricaría un descuadre."""
    _, sqls, prm = _saldos(monkeypatch, {"saldo_cierre": 1.0}, cuenta_id=CID)
    saldo_sql, saldo_prm = next((s, p) for s, p in zip(sqls, prm)
                                if "movimientos_manuales" not in s)
    assert "c.activa" not in saldo_sql
    assert saldo_prm == (FECHA, FECHA, CID)
