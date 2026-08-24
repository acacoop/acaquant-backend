"""tests/unit/test_postrade_seguridad.py — CONGELA las reglas del cliente Postrade.

Postrade no es como las otras integraciones del repo. Interbanking era 100% GET
y no podía mover plata ni por error; Postrade **sí puede**: `NewOrderSingle`
suscribe y rescata FCI, `CancelOrder` cancela, `AccountStatus` inactiva una
cuenta y `ChangePassword` nos deja afuera de nuestra propia integración.

Estos tests existen para que esas defensas no se aflojen sin que nadie se
entere. No prueban que la API funcione (eso lo miden los diags contra
producción): prueban que el CLIENTE no pueda mandar una orden por accidente y
que no dé por buena una respuesta que en realidad es un rechazo.

No tocan la red ni la base.
"""
from __future__ import annotations

import pytest

import config
from core import postrade
from core.postrade_catalogo import ESCRITURA, ESCRITURAS, LECTURA, LECTURAS, POR_NOMBRE


# --------------------------------------------------------------------------- #
# El sobre — verificado contra producción: el error viene con HTTP 200
# --------------------------------------------------------------------------- #
def test_sobre_ok_devuelve_value():
    assert postrade.desempaquetar(
        {"Status": "OK", "Code": "200", "Value": [1, 2]}, contexto="t"
    ) == [1, 2]


def test_sobre_unauthorized_es_error_de_auth():
    """El caso REAL medido en producción: HTTP 200 con el rechazo adentro.

    Si esto dejara de levantar, el cliente seguiría de largo con un token vacío
    y el fallo aparecería mucho más tarde y en otro lado.
    """
    with pytest.raises(postrade.PostradeAuthError):
        postrade.desempaquetar(
            {
                "Status": "Unauthorized",
                "Code": "401",
                "ErrorMessage": "Unauthorized",
                "ErrorDescription": '"Invalid Authorization"',
            },
            contexto="token",
        )


def test_sobre_403_es_no_habilitado():
    """Permiso denegado ≠ credenciales mal: son dos reclamos distintos y el
    código tiene que poder distinguirlos sin leer el texto del mensaje."""
    with pytest.raises(postrade.PostradeNoHabilitado):
        postrade.desempaquetar({"Status": "Forbidden", "Code": "403"}, contexto="x")


def test_sobre_error_generico():
    with pytest.raises(postrade.PostradeError):
        postrade.desempaquetar({"Status": "Error", "Code": "500"}, contexto="x")


def test_sobre_no_dict():
    with pytest.raises(postrade.PostradeError):
        postrade.desempaquetar([1, 2, 3], contexto="x")


def test_el_motivo_real_va_en_el_mensaje():
    """El detalle viaja en ErrorMessage/ErrorDescription, NO en Value (que en
    los errores llega null). Perderlo deja al operador sin saber qué pasó."""
    with pytest.raises(postrade.PostradeAuthError, match="Invalid Authorization"):
        postrade.desempaquetar(
            {"Status": "Unauthorized", "Code": "401",
             "ErrorDescription": '"Invalid Authorization"', "Value": None},
            contexto="token",
        )


# --------------------------------------------------------------------------- #
# Escritura — la doble llave
# --------------------------------------------------------------------------- #
def test_escribir_sin_confirmar_no_sale_a_la_red(monkeypatch):
    """Aunque el .env permita escribir, falta la llave del código."""
    monkeypatch.setattr(config, "POSTRADE_ESCRITURA", True)
    with pytest.raises(postrade.PostradeEscrituraBloqueada):
        postrade.escribir("NewOrderSingle", {"algo": 1})


def test_escribir_confirmado_pero_deshabilitado_no_sale_a_la_red(monkeypatch):
    """Aunque el código lo confirme, falta la llave del despliegue."""
    monkeypatch.setattr(config, "POSTRADE_ESCRITURA", False)
    with pytest.raises(postrade.PostradeEscrituraBloqueada):
        postrade.escribir("NewOrderSingle", {"algo": 1}, confirmo_escritura=True)


def test_las_dos_llaves_son_independientes(monkeypatch):
    """Ninguna de las dos alcanza sola. Este test es el que impide que alguien
    'simplifique' la doble llave dejando una sola."""
    for env, confirma in ((True, False), (False, True), (False, False)):
        monkeypatch.setattr(config, "POSTRADE_ESCRITURA", env)
        with pytest.raises(postrade.PostradeEscrituraBloqueada):
            postrade.escribir("CancelOrder", {}, confirmo_escritura=confirma)


def test_leer_rechaza_un_metodo_de_escritura(monkeypatch):
    """La puerta de lectura no puede ser un atajo para escribir."""
    monkeypatch.setattr(config, "POSTRADE_ESCRITURA", True)
    with pytest.raises(postrade.PostradeEscrituraBloqueada):
        postrade.leer("NewOrderSingle")


def test_escribir_rechaza_un_metodo_de_lectura():
    with pytest.raises(ValueError):
        postrade.escribir("CurrencyList", {}, confirmo_escritura=True)


# --------------------------------------------------------------------------- #
# Catálogo
# --------------------------------------------------------------------------- #
def test_metodo_inexistente_falla_con_la_lista():
    """Un typo tiene que reventar acá, no llegar a la API como un path
    inexistente que contesta 'no autorizado' y nos manda a reclamarle al
    proveedor algo que es nuestro."""
    with pytest.raises(KeyError, match="postrade_catalogo"):
        postrade.leer("CurrencyLst")


def test_no_hay_nombres_repetidos():
    from core.postrade_catalogo import TODOS
    nombres = [m.nombre for m in TODOS]
    assert len(nombres) == len(set(nombres)), "hay un método declarado dos veces"


def test_los_verbos_son_coherentes():
    assert all(m.verbo == LECTURA for m in LECTURAS)
    assert all(m.verbo == ESCRITURA for m in ESCRITURAS)


def test_los_metodos_peligrosos_estan_marcados_como_escritura():
    """Congela la clasificación de los que mueven plata o nos dejan afuera.

    Si alguien moviera uno de estos a LECTURAS, `leer()` lo dejaría pasar sin
    ninguna de las dos llaves. Es el único error de este archivo que no se
    notaría hasta que ya pasó.
    """
    for nombre in (
        "NewOrderSingle", "NewOrderList", "ReplaceOrder", "CancelOrder",
        "AccountRegistration", "AccountUpdate", "AccountStatus", "ChangePassword",
    ):
        assert POR_NOMBRE[nombre].verbo == ESCRITURA, f"{nombre} dejó de ser escritura"


def test_ninguna_lectura_apunta_a_un_path_de_escritura():
    """Las dos listas no pueden compartir path: sería una puerta trasera."""
    paths_escritura = {m.path for m in ESCRITURAS}
    for m in LECTURAS:
        assert m.path not in paths_escritura, f"{m.nombre} comparte path con un método de escritura"


# --------------------------------------------------------------------------- #
# Fechas
# --------------------------------------------------------------------------- #
def test_fecha_api_formatos():
    from datetime import date
    assert postrade.fecha_api(date(2026, 9, 1)) == "20260901"
    assert postrade.fecha_api("2026-09-01") == "20260901"
    assert postrade.fecha_api("20260901") == "20260901"


def test_fecha_api_rechaza_basura():
    """Una fecha mal puesta NO da error ruidoso del lado del proveedor: el
    manual dice que sin fecha devuelve 'la última información disponible'. O
    sea que el modo de fallar es contestar algo plausible y equivocado."""
    for mala in ("2026-9-1", "hoy", "010926", ""):
        with pytest.raises(ValueError):
            postrade.fecha_api(mala)


# --------------------------------------------------------------------------- #
# Parámetros obligatorios
# --------------------------------------------------------------------------- #
def test_falta_obligatorio_no_sale_a_la_red():
    """Sin EntryDate la API devolvería lo último que tenga, en vez de fallar —
    así que el que tiene que frenar es el cliente."""
    with pytest.raises(ValueError, match="EntryDate"):
        postrade.leer("ClosingProcesses")
