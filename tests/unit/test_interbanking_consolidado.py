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
    """La proyección pública se aplica igual venga el saldo de donde venga."""
    sin_base([_cuenta(saldo_banco=1,
                      account_cbu="0340000800000012345678", account_cuit="30712345678")])
    c = bancos.consolidado("x@y", FECHA)["bancos"][0]["cuentas"][0]
    plano = str(c)
    assert "0340000800000012345678" not in plano and "30712345678" not in plano
    assert "30410075359500020" not in plano
