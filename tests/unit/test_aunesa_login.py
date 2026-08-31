"""El login del custodio: qué se reintenta, qué NO, y el cortacircuito.

Aunesa devolvió HTTP 500 en su `POST /login` el 2026-08-07/09 y otra vez el
2026-08-20, sin que nada nuestro cambiara. De ahí salen las tres reglas que este
archivo congela, porque las tres se rompen en silencio:

  · un 5xx SE REINTENTA (si es intermitente, la vista se recupera sola);
  · un 4xx NO se reintenta (es credencial NUESTRA: machacarles el login con la
    clave mal es la forma más rápida de que nos bloqueen la cuenta);
  · después de un fallo hay un CORTACIRCUITO, si no cada poll de la vista (20s)
    y cada usuario pagan la tanda entera de reintentos con el `_lock` tomado.
"""
from __future__ import annotations

import pytest

from core import aunesa


class _Resp:
    """Lo mínimo de `requests.Response` que mira `_login`."""

    def __init__(self, status: int, body: dict | None = None, texto: str = ""):
        self.status_code, self._body, self.text = status, body, texto or str(body or "")

    def json(self):
        if self._body is None:
            raise ValueError("no es JSON")
        return self._body


ANOTADO: list[tuple[bool, str]] = []


@pytest.fixture(autouse=True)
def _limpio(monkeypatch):
    """Cada test arranca sin token, sin cortacircuito y sin dormir de verdad.

    `proveedores.anotar` se reemplaza por un espía: el de verdad escribe en
    Postgres, y lo que importa acá es QUÉ se anota, no que se persista.
    """
    aunesa.reset_estado()
    ANOTADO.clear()
    monkeypatch.setattr(aunesa.config, "AUNESA_CLIENT_ID", "cid")
    monkeypatch.setattr(aunesa.config, "AUNESA_USERNAME", "user")
    monkeypatch.setattr(aunesa.config, "AUNESA_PASSWORD", "secreta")
    monkeypatch.setattr(aunesa.time, "sleep", lambda _s: None)
    monkeypatch.setattr(aunesa.proveedores, "anotar",
                        lambda prov, *, ok, error="", donde="": ANOTADO.append((ok, error)))
    yield
    aunesa.reset_estado()


def _postea(monkeypatch, respuestas):
    """Encola respuestas (o excepciones) para los POST y devuelve el contador."""
    llamadas = []

    def _post(url, **kw):
        llamadas.append(url)
        r = respuestas[min(len(llamadas) - 1, len(respuestas) - 1)]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(aunesa.requests, "post", _post)
    return llamadas


def test_un_500_se_reintenta_y_termina_en_AunesaCaido(monkeypatch):
    llamadas = _postea(monkeypatch, [_Resp(500, texto="Internal Server Error")])
    with pytest.raises(aunesa.AunesaCaido) as e:
        aunesa.auth_headers()
    assert len(llamadas) == aunesa.LOGIN_INTENTOS, "un 5xx tiene que reintentarse"
    assert "servidor del custodio" in str(e.value), "el mensaje dice de quién es la culpa"


def test_intermitente_el_segundo_intento_salva_la_vista(monkeypatch):
    _postea(monkeypatch, [_Resp(500), _Resp(200, {"token": "T"})])
    assert aunesa.auth_headers()["Authorization"] == "Bearer T"


def test_un_401_NO_se_reintenta_y_no_es_AunesaCaido(monkeypatch):
    """Credencial nuestra: reintentar no la arregla y puede bloquear la cuenta."""
    llamadas = _postea(monkeypatch, [_Resp(401, texto="unauthorized")])
    with pytest.raises(RuntimeError) as e:
        aunesa.auth_headers()
    assert not isinstance(e.value, aunesa.AunesaCaido), "un 401 NO es 'Aunesa caído'"
    assert len(llamadas) == 1, "el 4xx corta en el primer intento"


def test_sin_credenciales_ni_se_toca_la_red(monkeypatch):
    monkeypatch.setattr(aunesa.config, "AUNESA_PASSWORD", None)
    llamadas = _postea(monkeypatch, [_Resp(200, {"token": "T"})])
    with pytest.raises(RuntimeError) as e:
        aunesa.auth_headers()
    assert "AUNESA_PASSWORD" in str(e.value), "el error nombra lo que falta"
    assert llamadas == [], "sin credenciales no hay nada que probar contra Aunesa"


def test_el_cortacircuito_no_vuelve_a_pegarle_a_Aunesa(monkeypatch):
    llamadas = _postea(monkeypatch, [_Resp(500)])
    for _ in range(3):
        with pytest.raises(aunesa.AunesaCaido):
            aunesa.auth_headers()
    assert len(llamadas) == aunesa.LOGIN_INTENTOS, (
        "los 3 polls tienen que costar UNA tanda de intentos, no tres")


def test_cuando_vence_el_cortacircuito_se_reintenta(monkeypatch):
    llamadas = _postea(monkeypatch, [_Resp(500)])
    with pytest.raises(aunesa.AunesaCaido):
        aunesa.auth_headers()
    # El reloj avanza más que la ventana: el cortacircuito tiene que cerrarse solo.
    real = aunesa.time.monotonic
    monkeypatch.setattr(aunesa.time, "monotonic",
                        lambda: real() + aunesa.FALLO_TTL_S + 1)
    with pytest.raises(aunesa.AunesaCaido):
        aunesa.auth_headers()
    assert len(llamadas) == 2 * aunesa.LOGIN_INTENTOS


def test_el_token_bueno_se_cachea(monkeypatch):
    llamadas = _postea(monkeypatch, [_Resp(200, {"token": "T"})])
    aunesa.auth_headers()
    aunesa.auth_headers()
    assert len(llamadas) == 1, "el token se reusa; solo un 401 fuerza el re-login"


def test_un_200_sin_token_no_pasa_por_bueno(monkeypatch):
    """Un gateway degradado puede contestar 200 con cualquier cosa adentro."""
    llamadas = _postea(monkeypatch, [_Resp(200, {"mensaje": "ok"})])
    with pytest.raises(aunesa.AunesaCaido):
        aunesa.auth_headers()
    assert len(llamadas) == aunesa.LOGIN_INTENTOS


def test_la_caida_queda_anotada_para_el_agente(monkeypatch):
    """El aviso del AV AGENT sale de acá (`AGENT.md` §0.ad): si el login no
    anota, el back office se entera solo si alguien abre la pantalla."""
    _postea(monkeypatch, [_Resp(500, texto="Internal Server Error")])
    with pytest.raises(aunesa.AunesaCaido):
        aunesa.auth_headers()
    assert ANOTADO and all(not ok for ok, _ in ANOTADO)
    assert any("500" in err for _, err in ANOTADO), "el motivo EXACTO, no 'falló'"


def test_la_credencial_vencida_se_anota_distinto_de_una_caida(monkeypatch):
    _postea(monkeypatch, [_Resp(401, texto="unauthorized")])
    with pytest.raises(RuntimeError):
        aunesa.auth_headers()
    assert any("rechazó las credenciales" in err for _, err in ANOTADO)


def test_el_login_bueno_anota_el_ok(monkeypatch):
    _postea(monkeypatch, [_Resp(200, {"token": "T"})])
    aunesa.auth_headers()
    assert ANOTADO == [(True, "")]


# ── EL CLIENT_ID VA VACÍO, Y SIEMPRE FUE ASÍ (2026-08-20) ────────────────────

def test_sin_CLIENT_ID_el_login_SI_se_intenta(monkeypatch):
    """**Exigirlo dejó a Aunesa muerto de NUESTRO lado.** El login cortaba antes
    de tocar la red y se caía todo lo que depende del custodio —Tesorería, saldos
    liquidados, tenencia del día, informes— con «faltan credenciales», apuntando
    al lugar equivocado.

    Y el modo de falla es el peor de todos: **no fallaba nada nuevo**. Aunesa ya
    devolvía 500, así que la vista ya decía CAÍDO; el cambio solo reemplazó una
    causa ajena por una propia sin que se notara la diferencia.

    La lección vale para cualquier credencial: **una validación de config que
    nunca se probó contra la config REAL es una hipótesis, no una guarda**
    (REGLA #2). Que el campo se llame `clientId` no significa que el proveedor lo
    pida, y meses de logins exitosos con el campo vacío son la medición que
    manda."""
    monkeypatch.setattr(aunesa.config, "AUNESA_CLIENT_ID", None)
    llamadas = _postea(monkeypatch, [_Resp(200, {"token": "T"})])
    aunesa.reset_estado()
    assert aunesa.auth_headers()["Authorization"] == "Bearer T"
    assert len(llamadas) == 1, "con client_id vacío el login TIENE que intentarse"


def test_el_client_id_viaja_TAL_CUAL_esta(monkeypatch):
    """No se lo reemplaza por `""` ni por nada: el payload que funcionó durante
    meses es el que se sigue mandando. Cambiarlo «por prolijidad» es tocar lo
    único que no se puede probar desde acá."""
    import inspect
    src = inspect.getsource(aunesa._login)
    assert '"clientId": config.AUNESA_CLIENT_ID,' in src
