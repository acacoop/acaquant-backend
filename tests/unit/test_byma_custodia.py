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


# ─────────────────────────────────────────────────────────────────────────────
# La ESCRITURA — las guardas que impiden que una foto vacía borre el día.
# ─────────────────────────────────────────────────────────────────────────────
from datetime import date

from core import custodia_escritura as ce


def test_cero_filas_no_borra_la_foto(monkeypatch, caplog):
    """Temprano BYMA contesta 200 sin filas. Si eso borrara el día, la pantalla
    diría «sin tenencia» con total seguridad — peor que mostrarla vieja."""
    def explotar():
        raise AssertionError("no tiene que tocar la base con 0 filas")
    monkeypatch.setattr(ce, "mapa_codigo_a_unidad", explotar)
    monkeypatch.setattr(ce, "get_pool", explotar)

    stats = ce.guardar(date(2026, 9, 14), [])
    assert stats["escrito"] == 0
    assert "no se toca" in stats["motivo"]


def test_filas_ilegibles_tampoco_borran(monkeypatch):
    """Llegaron filas pero ninguna tiene accountNumber usable. No saber LEER la
    respuesta no es lo mismo que no haya tenencia: tampoco se borra."""
    monkeypatch.setattr(ce, "mapa_codigo_a_unidad", dict)
    monkeypatch.setattr(ce, "get_pool",
                        lambda: (_ for _ in ()).throw(AssertionError("no debe escribir")))
    stats = ce.guardar(date(2026, 9, 14), [{"accountNumber": "sin-barra", "holding": "1"}])
    assert stats["escrito"] == 0
    assert stats["sin_cuenta_reconocible"] == 1


def test_cantidad_ilegible_es_none_y_no_cero():
    """Cero y «no sé» son cosas distintas: un 0 se suma y desaparece en un total."""
    assert ce._cantidad("580") == 580.0
    assert ce._cantidad("1.234,5") is None or isinstance(ce._cantidad("1234.5"), float)
    assert ce._cantidad(None) is None
    assert ce._cantidad("") is None
    assert ce._cantidad("n/d") is None


def test_el_trabajo_pendiente_se_reconoce_por_el_uuid_no_por_el_status():
    """BYMA contesta HTTP 202 con un "code": 409 ADENTRO del cuerpo.

    Mirar el status buscando un 409 no matchea nunca, y el job muere en el primer
    paso del baile del X-UUID. Pasó en la primera corrida real contra producción.
    """
    class R:
        status_code = 202
        def json(self):
            return {"message": "Response is not ready call later with uuid >> abc",
                    "code": 409, "uuid": "abc"}

    assert bc._uuid_de(R()) == "abc", "un 202 con uuid ES un trabajo pendiente"

    class SinUuid:
        status_code = 400
        def json(self):
            return {"error": "lo que sea"}

    assert bc._uuid_de(SinUuid()) is None

    class NoJson:
        status_code = 500
        def json(self):
            raise ValueError("no es json")

    assert bc._uuid_de(NoJson()) is None
