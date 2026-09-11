"""Las trampas del gateway de BYMA, congeladas.

Las cinco fueron MEDIDAS contra producción y ninguna está en el OpenAPI que
publica el proveedor. Si alguien "simplifica" el parser, estos tests dicen qué
se rompe y por qué estaba así.
"""
from __future__ import annotations

from core import byma_custodia as bc

COLS = bc.COLUMNAS_HOLDINGS


class _Resp:
    """Lo mínimo de requests.Response que usa `_parsear`."""

    def __init__(self, ctype: str, texto: str, js=None):
        self.headers = {"Content-Type": ctype}
        self.text = texto
        self._js = js

    def json(self):
        if self._js is None:
            raise ValueError("no es JSON")
        return self._js


def test_json_viene_envuelto_en_result():
    """El OpenAPI declara un objeto plano; lo real es {"meta":…, "result":[…]}."""
    js = {"meta": {"count": 1}, "result": [
        {"participantCode": "74", "accountNumber": "74/805", "cvsaIdentifier": "5921",
         "subBalanceType": "AVAILABLE", "holding": "580"}]}
    filas = bc._parsear(_Resp("application/json", "", js), COLS)
    assert len(filas) == 1
    assert filas[0]["cvsaIdentifier"] == "5921"


def test_meta_count_miente_y_se_usa_len(caplog):
    """Medido: count=3 sobre un result de 4. Se cuenta el array, no el campo."""
    js = {"meta": {"count": 3}, "result": [{"holding": str(i)} for i in range(4)]}
    filas = bc._parsear(_Resp("application/json", "", js), COLS)
    assert len(filas) == 4, "si esto devuelve 3, alguien volvió a confiar en meta.count"
    assert "meta.count" in caplog.text, "la divergencia tiene que quedar avisada"


def test_csv_se_separa_con_punto_y_coma():
    txt = "74;74/805;5921;AVAILABLE;580\n74;74/805;9422;AVAILABLE;3320\n"
    filas = bc._parsear(_Resp("text/csv", txt), COLS)
    assert len(filas) == 2
    assert filas[0] == {"participantCode": "74", "accountNumber": "74/805",
                        "cvsaIdentifier": "5921", "subBalanceType": "AVAILABLE",
                        "holding": "580"}


def test_csv_con_cabecera_no_la_cuenta_como_dato():
    """El gateway no garantiza cabecera, así que se detecta en vez de suponerla."""
    txt = ("participantCode;accountNumber;cvsaIdentifier;subBalanceType;holding\n"
           "74;74/805;5921;AVAILABLE;580\n")
    filas = bc._parsear(_Resp("text/csv", txt), COLS)
    assert len(filas) == 1
    assert filas[0]["holding"] == "580"


def test_id_cuenta_parte_por_la_barra():
    """REGLA #9: la cuenta sale de la FICHA (el lado derecho), no del string entero."""
    assert bc.id_cuenta("74/805") == "805"
    assert bc.id_cuenta(" 74/805 ") == "805"
    # Un formato distinto no se adivina: devuelve None para que se vea.
    assert bc.id_cuenta("805") is None
    assert bc.id_cuenta("74/") is None
    assert bc.id_cuenta("") is None


def test_no_expone_el_metodo_que_escribe():
    """`transactionsbyreference` ejecuta una tarea de ESCRITURA (ficha del portal).

    Un cliente de lectura no lo ofrece. Si alguien lo agrega, que sea a mano y
    leyendo por qué no estaba.
    """
    assert not hasattr(bc, "transactions_by_reference")
    assert "transactionsbyreference" not in dir(bc)
