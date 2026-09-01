"""De dónde sale el saldo de cada cuenta en el CONSOLIDADO BANCOS.

El consolidado tiene DOS fuentes para el mismo renglón y la regla de cuál gana no
puede quedar como comentario: si se invierte, el back office ve un número que
parece respaldado por el extracto y no lo está.

El caso que motivó todo esto es el de la cuenta QUIETA. El extracto **solo
devuelve los días CON movimientos**, así que con la ventana que usa el back
office (último día hábil + hoy) una cuenta que no se movió no tiene ninguna fila
y salía con «—». `bancos.saldos` la cubre, porque el banco informa el saldo se
haya movido o no.
"""
from __future__ import annotations

import pathlib
from datetime import date

import pytest

from api.services import bancos

FECHA = date(2026, 8, 18)


def _cuenta(**kw):
    base = {
        "id": 1, "bank_number": "034", "bank_name": "Patagonia",
        "account_number": "30410075359500020", "account_type": "CC",
        "currency": "ARS", "account_label": "ACA VALORES SA", "activa": True,
        "saldo_apertura": None, "saldo_cierre": None,
        "total_movimientos": None, "saldo_banco": None,
    }
    return {**base, **kw}


@pytest.fixture
def sin_base(monkeypatch):
    """Aísla el service de Postgres: la regla es pura, la base solo la alimenta."""
    def _fake_q(filas):
        def _q(sql, params=None):
            t = " ".join(str(sql).split())
            # `_saldos_banco`: arma el SALDO AL CIERRE con las mismas filas. Es
            # la MISMA función que sella, así que la pantalla y el sellado no
            # pueden dar distinto.
            if "AS informado" in t:
                # Solo el día de la vista tiene datos: el anterior viene vacío,
                # así que la apertura cae al `saldo_apertura` del extracto.
                if params and params[0] != FECHA:
                    return []
                return [{"cuenta_id": f["id"], "origen": f.get("origen"),
                         "saldo_cierre": f.get("saldo_cierre"),
                         "informado": f.get("saldo_banco"),
                         "ajuste": 0, "acumulado": 0}
                        for f in filas]
            if "c.bank_number" in t:
                return filas
            return []
        return _q

    def _instalar(filas):
        monkeypatch.setattr(bancos, "_q", _fake_q(filas))
        monkeypatch.setattr(bancos, "_exec", lambda sql, params=None: 1)
        monkeypatch.setattr(bancos, "_auditar", lambda *a, **k: None)
        monkeypatch.setattr("core.roles.get_user_role", lambda *a, **k: "sales")
    return _instalar


def test_con_extracto_manda_el_extracto(sin_base):
    sin_base([_cuenta(saldo_apertura=100, saldo_cierre=150, saldo_banco=150)])
    c = bancos.consolidado("x@y", FECHA)["bancos"][0]["cuentas"][0]
    assert c["fuente"] == "extracto"
    assert c["saldo_cierre"] == 150
    assert c["variacion"] == 50


def test_la_cuenta_QUIETA_sale_del_saldo_y_no_con_guion(sin_base):
    """EL caso. Sin extracto en el rango, pero el banco informa cuánto hay."""
    sin_base([_cuenta(saldo_banco=4321.55)])
    r = bancos.consolidado("x@y", FECHA)
    c = r["bancos"][0]["cuentas"][0]
    assert c["fuente"] == "saldo"
    assert c["saldo_cierre"] == 4321.55
    assert r["sin_datos"] == 0, "tiene saldo: no es una cuenta sin datos"
    assert r["bancos"][0]["cuentas"][0]["gastos_bancarios"] is None, (
        "la regla de gastos no está definida: None, nunca 0")


def test_sin_extracto_la_variacion_es_None_no_cero(sin_base):
    """No hay apertura contra la cual comparar. Cero sería inventar que no se movió."""
    sin_base([_cuenta(saldo_banco=999)])
    c = bancos.consolidado("x@y", FECHA)["bancos"][0]["cuentas"][0]
    assert c["variacion"] is None


def test_sin_ninguna_fuente_sigue_en_guion_y_cuenta_como_sin_datos(sin_base):
    sin_base([_cuenta()])
    r = bancos.consolidado("x@y", FECHA)
    c = r["bancos"][0]["cuentas"][0]
    assert c["fuente"] is None and c["saldo_cierre"] is None
    assert r["sin_datos"] == 1
    assert r["gastos_definidos"] is False


def test_si_las_dos_fuentes_no_coinciden_se_publica_la_diferencia(sin_base):
    """Las dos las informa el banco. Elegir una y tapar la otra sería esconder un
    hallazgo de conciliación, que es justamente para lo que existe esta vista."""
    sin_base([_cuenta(saldo_apertura=100, saldo_cierre=150, saldo_banco=140)])
    c = bancos.consolidado("x@y", FECHA)["bancos"][0]["cuentas"][0]
    assert c["discrepancia"] == 10
    assert c["fuente"] == "extracto", "el extracto sigue mandando; la diferencia se avisa"


def test_coincidiendo_no_hay_discrepancia(sin_base):
    sin_base([_cuenta(saldo_apertura=100, saldo_cierre=150, saldo_banco=150)])
    c = bancos.consolidado("x@y", FECHA)["bancos"][0]["cuentas"][0]
    assert c["discrepancia"] is None


def test_el_cbu_no_se_filtra_ni_por_esta_via(sin_base):
    """La proyección pública se aplica igual venga el saldo de donde venga.

    El NÚMERO sí sale entero (decisión del user, 2026-08-18); el CBU no. Es la
    misma línea que cuida `test_interbanking_seguridad`: identificar la cuenta y
    poder transferirle plata no son el mismo permiso.
    """
    sin_base([_cuenta(saldo_banco=1,
                      account_cbu="0340000800000012345678", account_cuit="30712345678")])
    c = bancos.consolidado("x@y", FECHA)["bancos"][0]["cuentas"][0]
    plano = str(c)
    assert "0340000800000012345678" not in plano, "se filtró el CBU"
    assert "30712345678" not in plano, "se filtró el CUIT"
    assert c["numero"] == "30410075359500020", "el número tiene que salir entero"


# --------------------------------------------------------------------------- #
# MOVIMIENTOS MANUALES — lo que el banco no informa
# --------------------------------------------------------------------------- #
# Interbanking no tiene todos los bancos de la casa, y el que falta igual mueve
# plata. Un movimiento manual SIEMPRE impacta el saldo al cierre: en una cuenta
# real se suma arriba de su extracto y en una manual es todo el saldo.
def _mock_manual(monkeypatch, filas_cuentas, manuales=(), previos=()):
    from api.services import bancos as svc

    def _q(sql, params=None):
        t = " ".join(str(sql).split())
        # `_saldos_banco` arma el SALDO AL CIERRE: es la misma función que sella.
        if "AS informado" in t:
            man = sum(m["ajuste"] for m in manuales)
            return [{"cuenta_id": f["id"], "origen": f.get("origen"),
                     "saldo_cierre": f.get("saldo_cierre"),
                     "informado": f.get("saldo_banco"),
                     "ajuste": man, "acumulado": man}
                    for f in filas_cuentas]
        # La APERTURA se LEE del cierre sellado de ayer.
        if "cierres_diarios" in t:
            return list(previos)
        if "FROM bancos.cuentas" in t:
            return filas_cuentas
        if "movimientos_manuales" in t:      # el CONTEO de manuales del día
            return [{"cuenta_id": 1, "n": len(manuales)}]
        if "gastos_baldes" in t:
            return []
        return []

    monkeypatch.setattr(svc, "_q", _q)
    monkeypatch.setattr(svc, "_exec", lambda sql, params=None: 1)
    monkeypatch.setattr("core.roles.get_user_role", lambda *a, **k: "sales")
    return svc


def _fila(out):
    return out["bancos"][0]["cuentas"][0]


def test_el_manual_se_SUMA_arriba_del_extracto(monkeypatch):
    """No reemplaza al saldo del banco: lo ajusta. Es plata que el banco no
    informa, no una corrección de lo que informó."""
    svc = _mock_manual(monkeypatch, [_cuenta(saldo_cierre=1000.0)],
                       [{"ajuste": 250.0}])
    c = _fila(svc.consolidado("x@y", FECHA))
    assert c["saldo_cierre"] == 1250.0
    assert c["fuente"] == "extracto"          # el origen del saldo NO cambia
    assert c["ajuste_manual"] == 250.0        # y se canta cuánto puso una persona


def test_un_banco_manual_arranca_de_cero(monkeypatch):
    """Sin extracto ni saldo del banco —el caso de un banco que no está en
    Interbanking— el saldo ES la suma de lo cargado a mano."""
    svc = _mock_manual(monkeypatch, [_cuenta()],
                       [{"ajuste": -400.0}])
    c = _fila(svc.consolidado("x@y", FECHA))
    assert c["fuente"] == "manual"
    assert c["saldo_cierre"] == -400.0


def test_sin_manuales_el_saldo_no_se_toca(monkeypatch):
    svc = _mock_manual(monkeypatch, [_cuenta(saldo_cierre=1000.0)])
    c = _fila(svc.consolidado("x@y", FECHA))
    assert c["saldo_cierre"] == 1000.0
    assert c["ajuste_manual"] is None


def test_una_cuenta_sin_nada_sigue_siendo_sin_dato(monkeypatch):
    """«No sabemos» no es «cero»: una cuenta sin extracto, sin saldo y sin
    manuales tiene que seguir mostrando «—»."""
    svc = _mock_manual(monkeypatch, [_cuenta()])
    out = svc.consolidado("x@y", FECHA)
    assert _fila(out)["saldo_cierre"] is None
    assert out["sin_datos"] == 1


def test_una_cuenta_de_interbanking_no_se_borra_a_mano(monkeypatch):
    """Las da de alta el job: borrarlas desde la vista sería pelearse con él
    todos los días, porque el próximo run las vuelve a crear."""
    svc = _mock_manual(monkeypatch, [])
    monkeypatch.setattr(svc, "_q", lambda sql, params=None: [
        {"id": 1, "bank_name": "X", "account_number": "1", "origen": "interbanking"}])
    with pytest.raises(ValueError, match="no se borra a mano"):
        svc.borrar_cuenta_manual("x@y", 1)


# --------------------------------------------------------------------------- #
# EL ARRASTRE ES DE UN DÍA — ni cero ni todos (decisión del back office 2026-08-27)
# --------------------------------------------------------------------------- #
# «Lo que hay un día pasa para el otro y listo». Las dos mitades de la regla
# tienen test, porque el modelo se rompió una vez por cada lado: primero no
# arrastraba nada (el saldo volvía al crudo de Interbanking), y después se probó
# acumulando TODO, que le suma al saldo una pila de movimientos que el banco ya
# tiene adentro de su propio número.

def test_la_apertura_es_NUESTRO_cierre_de_ayer(monkeypatch):
    """Literal del back office: «el saldo inicial de hoy sería el saldo final de
    ayer». No es el `saldo_apertura` del extracto —eso es lo que declara el
    banco, y viaja aparte en `saldo_inicio_banco` para que DIFERENCIAS pueda
    mirar el salto entre los dos."""
    svc = _mock_manual(monkeypatch,
                       [_cuenta(saldo_apertura=1000.0, saldo_cierre=1000.0)],
                       previos=[{"cuenta_id": 1, "saldo": 1250.0,
                                 "fuente": "extracto", "ajuste_manual": 250.0}])
    c = _fila(svc.consolidado("x@y", FECHA))
    assert c["saldo_inicio"] == 1250.0, "el cierre SELLADO de ayer, leído tal cual"
    assert c["saldo_inicio_banco"] == 1000.0, "y el del banco no se pierde"


def test_el_cierre_usa_el_manual_DEL_DIA_no_el_acumulado(monkeypatch):
    """⚠️⚠️ **EL BUG QUE ENCONTRÓ EL BACK OFFICE (2026-08-27).** El saldo que
    informa Interbanking **ya trae adentro** los movimientos de días anteriores.
    Si el cierre les suma además todos los manuales históricos, da MÁS que
    «Interbanking + los manuales del día» y la diferencia entre dos días no se
    explica con NADA de lo que la pantalla muestra — que fue exactamente el
    síntoma.

    Acá el banco cierra en 1.000, hay 250 de manuales viejos y 0 cargados hoy:
    el cierre tiene que ser 1.000, no 1.250.
    """
    svc = _mock_manual(monkeypatch, [_cuenta(saldo_cierre=1000.0)],
                       [{"ajuste": 0.0}])
    c = _fila(svc.consolidado("x@y", FECHA))
    assert c["saldo_cierre"] == 1000.0, "el manual viejo YA está en el saldo del banco"


def test_lo_del_dia_SI_se_suma(monkeypatch):
    """La otra mitad: lo que se carga hoy todavía no lo tiene el banco."""
    svc = _mock_manual(monkeypatch, [_cuenta(saldo_cierre=1000.0)],
                       [{"ajuste": 250.0}])
    c = _fila(svc.consolidado("x@y", FECHA))
    assert c["saldo_cierre"] == 1250.0, "banco + lo de HOY, y nada de lo viejo"


def test_la_cuenta_que_el_banco_NO_informa_si_acumula(monkeypatch):
    """La excepción, y la razón por la que existe: sin extracto ni saldo no hay
    ningún número del banco que absorba los manuales viejos. Ahí el saldo ES el
    acumulado — si no, la cuenta volvería a cero teniendo la plata."""
    svc = _mock_manual(monkeypatch, [_cuenta()],
                       [{"ajuste": 100_000.0}])
    c = _fila(svc.consolidado("x@y", FECHA))
    assert c["fuente"] == "manual"
    assert c["saldo_cierre"] == 100_000.0


# --------------------------------------------------------------------------- #
# VISTA — el saldo de la pantalla es el NUESTRO, no el crudo del extracto
# --------------------------------------------------------------------------- #
def test_la_vista_muestra_el_saldo_CON_los_manuales(monkeypatch):
    """⚠️ Salía crudo de `extracto_dia.saldo_cierre`, así que una cuenta con
    movimientos manuales mostraba acá un número y otro en el CONSOLIDADO —misma
    cuenta, mismo día— y el de acá era el que ignoraba la carga del back office.

    El del banco no se pierde: viaja en `saldo_final_banco`, que es la evidencia
    independiente contra la que se concilia.
    """
    from api.services import bancos as svc

    def _q(sql, params=None):
        t = " ".join(str(sql).split())
        if "FROM bancos.extracto_dia" in t and "e.saldo_apertura" in t:
            return [{"fecha": FECHA, "saldo_apertura": 900.0, "saldo_cierre": 1000.0,
                     "total_creditos": 0, "total_debitos": 0, "total_movimientos": 0,
                     "cierra": True, "diferencia": 0, "sincronizado_at": None,
                     "movimientos_base": 0}]
        if "FROM bancos.movimientos_manuales" in t:
            return [{"id": 1, "fecha": FECHA, "descripcion": "cheque",
                     "importe": 250.0, "tipo": "C", "creado_por": "x@y",
                     "creado_at": None}]
        return []

    monkeypatch.setattr(svc, "_q", _q)
    monkeypatch.setattr(svc, "_exec", lambda sql, params=None: 1)
    r = svc.vista("x@y", 1, FECHA)["resumen"]
    assert r["saldo_final"] == 1250.0, "el saldo de la pantalla lleva el manual"
    assert r["saldo_final_banco"] == 1000.0, "y el del banco viaja aparte"
    assert r["ajuste_manual"] == 250.0


# --------------------------------------------------------------------------- #
# EL CIERRE SELLADO — un valor con fecha y banco, que se LEE al otro día
# --------------------------------------------------------------------------- #
# Pedido del back office (2026-08-27): «el saldo al cierre tiene que quedar como
# un valor con fecha y banco y usarse al otro día, no hay que hacer cálculos
# raros». Cada vez que la apertura se RECALCULABA aparecía una forma nueva de
# equivocarse: primero no sumaba los manuales, después los sumaba todos.

def test_la_apertura_se_LEE_del_sellado_y_no_se_recalcula(monkeypatch):
    """⚠️ El valor sellado MANDA, aunque no coincida con lo que daría recalcular.
    Es la garantía de que el saldo inicial de hoy es exactamente el número que
    ayer se mostró como cierre: si la apertura pudiera recalcularse, volvería a
    poder diferir del cierre que el back office ya miró y dio por bueno.
    """
    from api.services import bancos as svc

    def _q(sql, params=None):
        t = " ".join(str(sql).split())
        if "cierres_diarios" in t:
            return [{"cuenta_id": 1, "saldo": 999_999.0, "fuente": "extracto",
                     "ajuste_manual": 0.0}]
        if "AS informado" in t:
            return [{"cuenta_id": 1, "origen": "interbanking", "saldo_cierre": 1.0,
                     "informado": None, "ajuste": 0, "acumulado": 0}]
        if "FROM bancos.cuentas" in t:
            return [_cuenta(saldo_apertura=1.0, saldo_cierre=1.0)]
        return []

    monkeypatch.setattr(svc, "_q", _q)
    monkeypatch.setattr(svc, "_exec", lambda sql, params=None: 1)
    monkeypatch.setattr("core.roles.get_user_role", lambda *a, **k: "sales")
    c = svc.consolidado("x@y", FECHA)["bancos"][0]["cuentas"][0]
    assert c["saldo_inicio"] == 999_999.0, "manda el sellado, no el recálculo"


def test_si_el_dia_no_esta_sellado_se_sella_al_vuelo(monkeypatch):
    """Hace falta para que la pantalla no muestre «—» el primer día después del
    deploy, ni con un día viejo que nunca se selló."""
    from api.services import bancos as svc
    escrituras = []
    leidas = {"n": 0}

    def _q(sql, params=None):
        t = " ".join(str(sql).split())
        # ⚠️ `_saldos_banco` JOINEA `cierres_diarios`, así que se lo reconoce por
        # su alias propio y va primero.
        if "AS informado" in t:
            return [{"cuenta_id": 1, "origen": "interbanking", "saldo_cierre": 500.0,
                     "informado": None, "ajuste": 0, "acumulado": 0}]
        if "cierres_diarios" in t:
            leidas["n"] += 1
            return []          # el día no está sellado → hay que sellarlo
        if "FROM bancos.cuentas" in t:
            return [_cuenta(saldo_cierre=1.0)]
        return []

    monkeypatch.setattr(svc, "_q", _q)
    monkeypatch.setattr(svc, "_exec",
                        lambda sql, params=None: escrituras.append(sql) or 1)
    monkeypatch.setattr("core.roles.get_user_role", lambda *a, **k: "sales")
    c = svc.consolidado("x@y", FECHA)["bancos"][0]["cuentas"][0]
    assert any("cierres_diarios" in " ".join(str(e).split()) for e in escrituras), \
        "tiene que haber sellado"
    assert c["saldo_inicio"] == 500.0


# --------------------------------------------------------------------------- #
# CUÁL de los dos saldos de la API de Saldos es «el saldo del día»
# --------------------------------------------------------------------------- #
# `bancos.saldos` guarda dos números que el banco manda por bloques distintos:
#   · `saldo_dia`       — una fila POR DÍA (`historical_balances`). Cuánto quedó
#                         ese día: lo homogéneo con un cierre.
#   · `saldo_operativo` — la foto de HOY (`balances.current_operating_balance`).
#                         Lo DISPONIBLE ahora, no el cierre contable de una fecha.
#
# Hasta el 2026-09-01 el operativo le ganaba al del día cuando existía, o sea
# justo en la fecha que la pantalla muestra: el badge ≠ del consolidado comparaba
# el cierre del extracto contra el operativo y cantaba como contradicción del
# banco lo que era una diferencia de definición.

def test_manda_el_SALDO_DEL_DIA_y_no_el_operativo():
    """La decisión, congelada. Si se invierte, no falla nada: la columna muestra
    otro número y el ≠ aparece o desaparece sin que nadie lo pida."""
    assert bancos._SALDO_INFORMADO == "coalesce(s.saldo_dia, s.saldo_operativo)"


def test_el_operativo_SIGUE_siendo_el_respaldo():
    """El `coalesce` se conserva —al revés— y no es un detalle: una cuenta QUIETA
    no tiene fila en `historical_balances`, así que su único saldo es el operativo
    de la foto. Sin el fallback esa cuenta volvería a mostrar «—», que es
    exactamente el agujero que la API de Saldos vino a tapar."""
    assert "saldo_operativo" in bancos._SALDO_INFORMADO


def test_la_regla_se_declara_UNA_sola_vez():
    """⚠️ Estaba copiada en TRES queries (el cierre, el consolidado y
    DIFERENCIAS). Tres copias de una regla sin árbitro es la REGLA #9: el día que
    alguien corrija una, las otras dos siguen diciendo lo de antes y ninguna
    falla — muestran otro número en otra pantalla."""
    src = pathlib.Path(bancos.__file__).read_text(encoding="utf-8")
    sueltas = [ln for ln in src.split("\n")
               if "saldo_operativo" in ln and "saldo_dia" in ln
               and "_SALDO_INFORMADO =" not in ln]
    assert not sueltas, (
        "hay un coalesce de saldos escrito a mano en vez de usar "
        "`_SALDO_INFORMADO`:\n  " + "\n  ".join(s.strip() for s in sueltas))
    # la declaración + sus tres usos
    assert src.count("_SALDO_INFORMADO") >= 4
