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
        llamadas = {"n": 0}

        def _q(sql, params=None):
            llamadas["n"] += 1
            return filas if llamadas["n"] == 1 else []  # 2ª llamada = ultima_sync()
        return _q

    def _instalar(filas):
        monkeypatch.setattr(bancos, "_q", _fake_q(filas))
        monkeypatch.setattr(bancos, "_auditar", lambda *a, **k: None)
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
# La FOTO del día — lo que hace que un día cerrado sobreviva a la retención
# --------------------------------------------------------------------------- #
# ⚠️ Acá la foto NO existe por el mismo motivo que en Tesorería. Allá la vista se
# arma en vivo contra Aunesa y sin foto el día se pierde. Acá el dato SÍ está en
# la base… pero `bancos.*` retiene solo 3 fechas: al cuarto día el consolidado de
# un día cerrado desaparece. La foto es lo que lo hace durar.
def _mock(monkeypatch, filas_cuentas, foto=None):
    """Base mockeada: cuentas + (opcional) una foto guardada de ese día."""
    from api.services import bancos as svc

    def _q(sql, params=None):
        t = " ".join(str(sql).split())
        if "FROM bancos.cuentas" in t:
            return filas_cuentas
        if "FROM bancos.snapshots" in t:
            return [foto] if foto else []
        if "gastos_baldes" in t:
            return [{"clave": "iva", "etiqueta": "IVA", "grupo": "concepto",
                     "orden": 10, "matcher_id": 1, "campo": "descripcion_ib",
                     "operador": "igual", "valor": "IVA"}]
        return []

    monkeypatch.setattr(svc, "_q", _q)
    monkeypatch.setattr(svc, "_exec", lambda sql, params=None: 1)
    monkeypatch.setattr("core.roles.get_user_role", lambda *a, **k: "sales")
    return svc


def test_un_dia_con_datos_NO_usa_la_foto(monkeypatch):
    """La foto es el último recurso: mientras el día esté en la base, manda la
    base. Si no, una foto vieja podría tapar un dato corregido después."""
    svc = _mock(monkeypatch, [_cuenta(saldo_cierre=100.0)],
                foto={"fecha": FECHA, "tomado_at": __import__("datetime").datetime(2026, 8, 18),
                      "tomado_por": "x@y", "hash_sha256": "x", "datos": {"bancos": []}})
    out = svc.consolidado("x@y", FECHA)
    assert out["es_foto"] is False
    assert out["bancos"][0]["cuentas"][0]["saldo_cierre"] == 100.0


def test_sin_dato_en_la_base_manda_la_foto(monkeypatch):
    """El caso para el que existe: pasaron 3 días, la retención purgó el día y el
    consolidado quedaría en blanco."""
    import datetime as _dt

    congelado = {"fecha": FECHA.isoformat(), "bancos": [{"banco": "034",
                 "banco_nombre": "Patagonia", "cuentas": [{"saldo_cierre": 7.0}]}],
                 "cuentas": 1, "sin_datos": 0}
    svc = _mock(monkeypatch, [_cuenta()],
                foto={"fecha": FECHA, "tomado_at": _dt.datetime(2026, 8, 18),
                      "tomado_por": "x@y", "hash_sha256": "roto", "datos": congelado})
    out = svc.consolidado("x@y", FECHA)
    assert out["es_foto"] is True
    assert out["bancos"][0]["cuentas"][0]["saldo_cierre"] == 7.0
    # El hash guardado no coincide con el payload → alguien lo editó por fuera de
    # la API. La foto se muestra igual (el dato es el que hay) y se canta.
    assert out["foto"]["hash_ok"] is False


def test_lo_que_depende_de_quien_mira_NO_se_congela(monkeypatch):
    """`puede_escribir` y quién está en línea son del presente: si viajaran dentro
    de la foto, mañana la abriría otro y vería los permisos del que la sacó."""
    import datetime as _dt

    svc = _mock(monkeypatch, [_cuenta()],
                foto={"fecha": FECHA, "tomado_at": _dt.datetime(2026, 8, 18),
                      "tomado_por": "x@y", "hash_sha256": "x",
                      "datos": {"bancos": [], "puede_escribir": True,
                                "conectados": [{"email": "viejo@y"}]}})
    out = svc.consolidado("otro@y", FECHA)
    assert out["conectados"] == []
    assert out["puede_escribir"] is False


def test_no_se_saca_foto_de_un_dia_sin_saldos(monkeypatch):
    """Una foto en blanco es peor que no tener foto: al cuarto día el consolidado
    mostraría ceros con cara de dato bueno."""
    svc = _mock(monkeypatch, [_cuenta()])
    with pytest.raises(ValueError, match="no hay qué congelar"):
        svc.sacar_foto("x@y", FECHA)


# --------------------------------------------------------------------------- #
# MOVIMIENTOS MANUALES — lo que el banco no informa
# --------------------------------------------------------------------------- #
# Interbanking no tiene todos los bancos de la casa, y el que falta igual mueve
# plata. Un movimiento manual SIEMPRE impacta el saldo al cierre: en una cuenta
# real se suma arriba de su extracto y en una manual es todo el saldo.
def _mock_manual(monkeypatch, filas_cuentas, manuales=()):
    from api.services import bancos as svc

    def _q(sql, params=None):
        t = " ".join(str(sql).split())
        if "FROM bancos.cuentas" in t:
            return filas_cuentas
        if "movimientos_manuales" in t:
            return list(manuales)
        if "gastos_baldes" in t:
            return []
        if "FROM bancos.snapshots" in t:
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
                       [{"cuenta_id": 1, "ajuste": 250.0, "n": 2}])
    c = _fila(svc.consolidado("x@y", FECHA))
    assert c["saldo_cierre"] == 1250.0
    assert c["fuente"] == "extracto"          # el origen del saldo NO cambia
    assert c["ajuste_manual"] == 250.0        # y se canta cuánto puso una persona


def test_un_banco_manual_arranca_de_cero(monkeypatch):
    """Sin extracto ni saldo del banco —el caso de un banco que no está en
    Interbanking— el saldo ES la suma de lo cargado a mano."""
    svc = _mock_manual(monkeypatch, [_cuenta()],
                       [{"cuenta_id": 1, "ajuste": -400.0, "n": 1}])
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
