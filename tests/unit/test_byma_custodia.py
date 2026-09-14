"""Las trampas del gateway de BYMA, congeladas.

Las cinco fueron MEDIDAS contra producción y ninguna está en el OpenAPI que
publica el proveedor. Si alguien "simplifica" el parser, estos tests dicen qué
se rompe y por qué estaba así.
"""
from __future__ import annotations

import pytest

from api.services import custodia_sql as cs
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


def test_el_post_por_referencia_es_una_LECTURA():
    """La ficha del portal dice "ejecuta una tarea de ESCRITURA". Es boilerplate.

    La documentación real de BYMA lo define como consultar movimientos filtrando
    por `instructionReference`. Manda el filtro en el cuerpo porque una lista de
    N referencias no entra en una query string, nada más. Este módulo sigue
    siendo 100% de lectura y el método es parte de él.
    """
    assert hasattr(bc, "transactions_by_reference")
    # Sin referencias no sale ni un request: una lista vacía no es "traeme todo".
    assert bc.transactions_by_reference([]) == []
    assert bc.transactions_by_reference(["", "  "]) == []


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


# ─────────────────────────────────────────────────────────────────────────────
# Trampas 6 y 7 — lo que la documentación de BYMA corrige del portal
# ─────────────────────────────────────────────────────────────────────────────
def test_today_es_asincrono_y_lleva_el_csv_en_el_path(monkeypatch):
    """El portal dice "la respuesta será inmediata". Es falso: hace el baile.

    La primera versión de este módulo la implementó con `_get` (inmediato) y
    habría muerto en el primer 202 — el mismo modo de falla que ya nos comió
    medio día con `/holdings`.
    """
    llamadas: list = []

    def fake(path, params, *, columnas, **kw):
        llamadas.append((path, columnas))
        return []

    monkeypatch.setattr(bc, "_get_async", fake)
    monkeypatch.setattr(bc, "_get", lambda *a, **k: pytest.fail("today NO es inmediato"))
    bc.transactions_today(participant_code="74")

    path, columnas = llamadas[0]
    assert path == "transactions/today.csv/", "el .csv va adentro del path, con barra final"
    assert columnas is bc.COLUMNAS_TRANSACTIONS_TODAY
    assert len(columnas) == 11, "today trae 2 columnas de contraparte que el OpenAPI no declara"


def test_currency_es_un_codigo_y_no_un_nombre(caplog):
    """El diccionario del portal dice "nombre completo de la moneda". Viene 0/1/2."""
    assert bc.moneda("0") == "ARS"
    assert bc.moneda("1") == "USD"
    assert bc.moneda("2") == "USD-Trf"
    assert bc.moneda(None) is None
    assert bc.moneda("") is None
    # Un código nuevo NO se inventa: None y aviso. El crudo lo guarda el writer.
    with caplog.at_level("WARNING"):
        assert bc.moneda("9") is None
    assert "desconocido" in caplog.text


def test_la_cabecera_en_MAYUSCULA_no_entra_como_dato():
    """`/holdings` manda la cabecera en camel y `today` en mayúscula sostenida.

    Comparando tal cual, la de `today` entra como fila y aparece un movimiento
    fantasma con volumen None. No falla nada: solo queda mal.
    """
    csv_today = ("PARTICIPANTCODE;SETTLEMENTDATE;ACCOUNTNUMBER;CVSAIDENTIFIER;"
                 "SECURITIESSUBBALANCETYPE;VOLUME;AMOUNT;CURRENCY;"
                 "INSTRUCTIONREFERENCE;COUNTERPARTY;COUNTERPARTYSECURITIESACC\n"
                 "6;2026-04-10;6/3;5921;AVAILABLE;-280958.5138;0;1;SUSC20260410272;;\n")
    filas = bc._parsear(_Resp("text/csv", csv_today), bc.COLUMNAS_TRANSACTIONS_TODAY)
    assert len(filas) == 1, "la cabecera se descarta aunque venga en MAYÚSCULA"
    assert filas[0]["instructionReference"] == "SUSC20260410272"


# ─────────────────────────────────────────────────────────────────────────────
# PARTIDA DOBLE — el hallazgo que define el modelo de datos
# ─────────────────────────────────────────────────────────────────────────────
def test_el_signo_del_volumen_es_el_dato_y_no_se_pierde():
    """Cada referencia viene DOS veces con signo opuesto: una pata por cuenta.

    Medido contra producción: `6/3` → -280958.5138 y `6/600613` → +280958.5138,
    misma `SUSC20260410272`. Si la PK fuera la referencia sola, una de las dos
    patas se pierde EN SILENCIO — y el número queda mal sin que falle nada.
    """
    filas = [
        {"accountNumber": "6/3", "settlementDate": "2026-04-10", "cvsaIdentifier": "5921",
         "securitiesSubBalanceType": "AVAILABLE", "volume": "-280958.5138",
         "amount": "0", "currency": "1", "instructionReference": "SUSC20260410272"},
        {"accountNumber": "6/600613", "settlementDate": "2026-04-10", "cvsaIdentifier": "5921",
         "securitiesSubBalanceType": "AVAILABLE", "volume": "280958.5138",
         "amount": "0", "currency": "1", "instructionReference": "SUSC20260410272"},
    ]
    claves = ce.contar_claves([
        {"referencia": f["instructionReference"], "id_cuenta": bc.id_cuenta(f["accountNumber"]),
         "cvsa_id": f["cvsaIdentifier"], "sub_balance_type": f["securitiesSubBalanceType"]}
        for f in filas])
    assert claves["filas"] == 2
    assert claves["referencia"] == 1, "la referencia SOLA colapsa las dos patas: no sirve de PK"
    assert claves["referencia+id_cuenta"] == 2, "con la cuenta, las dos patas sobreviven"


def test_las_patas_se_pliegan_en_un_movimiento_con_entrega_y_recibe():
    """La tabla guarda patas; la pantalla muestra movimientos. El plegado es del
    backend — el front de esta app no deriva ni suma nada."""
    from datetime import date as _d

    f = _d(2026, 4, 10)
    patas = [
        # (fecha, ref, cuenta, cvsa, unidad, sub, volumen, monto, moneda, cod,
        #  contraparte, contraparte_cta, estado, motivo, fuente, actualizado)
        (f, "SUSC20260410272", "3", "5921", "AL30", "AVAILABLE", -280958.5138, 0,
         "USD", "1", None, None, None, None, "today", None),
        (f, "SUSC20260410272", "600613", "5921", "AL30", "AVAILABLE", 280958.5138, 0,
         "USD", "1", "BANCO X", "6/600613", "Settled", None, "byreference", None),
    ]
    out = cs._plegar(patas, fecha=f, dias=1)
    assert out["total"] == 1, "dos patas son UN movimiento"
    assert out["patas"] == 2
    m = out["movimientos"][0]
    assert m["entrega"] == "3" and m["recibe"] == "600613"
    assert m["volumen"] == 280958.5138, "el volumen del movimiento va en positivo"
    assert m["descalce"] is False, "las dos patas netean a cero"
    # Lo que trae un solo método completa el movimiento sin importar el orden.
    assert m["estado"] == "Settled" and m["contraparte"] == "BANCO X"
    assert out["sin_par"] == 0


def test_una_pata_sola_no_es_un_descalce():
    """Un movimiento contra una cuenta de otro agente solo tiene UNA pata nuestra.

    Marcarlo como partida rota sería inventar un error donde no lo hay.
    """
    from datetime import date as _d

    f = _d(2026, 4, 10)
    out = cs._plegar([(f, "REF1", "3", "5921", "AL30", "AVAILABLE", -100.0, 0,
                       "ARS", "0", None, None, None, None, "today", None)],
                     fecha=f, dias=1)
    m = out["movimientos"][0]
    assert m["patas"] == 1 and m["descalce"] is False
    assert out["sin_par"] == 1, "se cuenta aparte para que la pantalla lo pueda decir"
