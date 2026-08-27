"""tests/unit/test_ext_api.py — invariantes de la API EXTERNA (`/ext`).

Estos tests NO comprueban que la API "ande": comprueban que **no se pueda
romper la seguridad sin que falle algo**. Cada uno congela una decisión que, si
se deshace por descuido, no produce ningún error visible — sólo entrega datos de
más, en silencio, y nos enteraríamos por el accionista.

Los invariantes:
  1. Ningún endpoint de datos es alcanzable sin token (estructural: se listan
     las rutas de la app, no se testea endpoint por endpoint).
  2. Un cliente SIN cuentas autorizadas recibe 403, nunca "ve todo" (fail-closed
     — es la inversión deliberada de `core/grupos.py::cuentas_visibles`).
  3. Pedir una cuenta ajena es 403, no una lista vacía.
  4. Revocar una key mata sus tokens ya emitidos, en el acto.
  5. El arancel viaja SÓLO si el cliente lo tiene habilitado.
  6. Los boletos ANULADOS nunca salen, y ningún campo interno tampoco.
  7. Los montos viajan como número.
"""
from __future__ import annotations

import decimal

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import config
from api.ext import auth as ext_auth
from api.ext import db as ext_db
from api.ext import lectura

_SECRET = "test-secret-para-los-tests"


@pytest.fixture(autouse=True)
def _secreto(monkeypatch):
    """La app se comporta como configurada, sin tocar el .env real."""
    monkeypatch.setattr(config, "EXT_JWT_SECRET", _SECRET, raising=False)
    monkeypatch.setattr(ext_auth, "EXT_JWT_SECRET", _SECRET)
    monkeypatch.setattr(ext_db, "EXT_JWT_SECRET", _SECRET)


@pytest.fixture
def client():
    from api.ext.app import ext_app
    return TestClient(ext_app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _sin_auditoria(monkeypatch):
    """La auditoría escribe en Postgres; en unit no hay base."""
    monkeypatch.setattr(ext_db, "log_request", lambda **kw: None)
    monkeypatch.setattr(ext_db, "marcar_uso", lambda p: None)


def _token(cliente_id="cli_test", kid="avk_live_aaaa", secret=_SECRET, **extra):
    payload = {
        "iss": config.EXT_ISSUER, "sub": cliente_id, "kid": kid,
        "iat": 1000, "exp": 9_999_999_999,
    }
    payload.update(extra)
    return jwt.encode(payload, secret, algorithm="HS256")


def _montar_cliente(monkeypatch, *, cuentas=("10452",), activo=True,
                    key_viva=True, aranceles=False):
    monkeypatch.setattr(ext_db, "key_vigente", lambda p: key_viva)
    monkeypatch.setattr(ext_db, "cliente_por_id", lambda cid: {
        "id": cid, "nombre": "PEPITO SRL", "activo": activo,
        "ip_allowlist": [], "ver_aranceles": aranceles,
    })
    monkeypatch.setattr(ext_db, "cuentas_autorizadas", lambda cid: tuple(cuentas))


# ── 1. estructural: nada de datos sin token ──────────────────────────────────
def test_todo_endpoint_de_datos_exige_token(client):
    """Ninguna ruta nueva puede quedar accesible sin autenticar por descuido.

    La allowlist es explícita y corta: pedir el token (que trae su propia
    credencial), el health y la documentación. Cualquier otra cosa que alguien
    agregue mañana hace fallar este test hasta que declare su dependency.
    """
    libres = {"/v1/auth/token", "/v1/health",
              "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
    from api.ext.app import ext_app

    for ruta in ext_app.routes:
        path = getattr(ruta, "path", "")
        metodos = getattr(ruta, "methods", set())
        if path in libres or not metodos:
            continue
        assert "GET" in metodos, f"ruta inesperada: {path}"
        r = client.get(path)
        assert r.status_code == 401, f"{path} contestó {r.status_code} SIN token"
        assert r.json()["detail"]["code"] == "token_faltante"


@pytest.mark.parametrize("header", [
    None,                                   # sin header
    "Bearer no-es-un-jwt",                  # basura
    "Basic dXNlcjpwYXNz",                   # otro esquema
])
def test_token_invalido_es_401(client, header):
    r = client.get("/v1/cuentas", headers={"Authorization": header} if header else {})
    assert r.status_code == 401


def test_token_firmado_con_otro_secreto_es_401(client, monkeypatch):
    """Un JWT bien formado pero de otra firma no entra: se valida la firma, no la forma."""
    _montar_cliente(monkeypatch)
    r = client.get("/v1/cuentas",
                   headers={"Authorization": f"Bearer {_token(secret='otro')}"})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "token_invalido"


# ── 2. fail-closed: sin cuentas, 403 ─────────────────────────────────────────
def test_cliente_sin_cuentas_recibe_403_y_no_ve_todo(client, monkeypatch):
    """EL invariante. Un scope vacío NUNCA puede significar "todas las cuentas".

    Es lo contrario de `cuentas_visibles`, que ante un problema devuelve None =
    ve todo. Adentro de la mesa esa decisión es defendible; acá sería entregarle
    a un accionista los boletos de la mesa entera sin que nada falle.
    """
    _montar_cliente(monkeypatch, cuentas=())
    r = client.get("/v1/cuentas", headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "sin_cuentas"


def test_cliente_desactivado_no_entra(client, monkeypatch):
    _montar_cliente(monkeypatch, activo=False)
    r = client.get("/v1/cuentas", headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 401


# ── 3. no se puede pedir una cuenta ajena ────────────────────────────────────
def test_pedir_cuenta_ajena_es_403_no_lista_vacia(client, monkeypatch):
    """Un 200 vacío le haría creer que la cuenta existe y no operó. Es 403."""
    _montar_cliente(monkeypatch, cuentas=("10452",))
    r = client.get("/v1/operaciones?cuenta=99999",
                   headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "cuenta_no_autorizada"


def test_el_scope_llega_al_lector_aunque_no_se_pida_cuenta(client, monkeypatch):
    """Sin filtro de cuenta NO significa "todas": significa "todas las SUYAS"."""
    _montar_cliente(monkeypatch, cuentas=("10452", "10453"))
    visto = {}

    def _fake(**kw):
        visto.update(kw)
        return {"operaciones": [], "paginacion": {
            "limit": 500, "devueltas": 0, "hay_mas": False, "siguiente_cursor": None}}

    monkeypatch.setattr(lectura, "operaciones", _fake)
    r = client.get("/v1/operaciones", headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 200
    assert visto["cuentas"] == ("10452", "10453")


def test_resolver_cuentas_intersecta_lo_pedido():
    cli = ext_auth.Cliente(id="c", nombre="n", prefijo="p",
                           cuentas=("1", "2", "3"), ver_aranceles=False)
    assert ext_auth.resolver_cuentas(cli, "2,3") == ("2", "3")
    assert ext_auth.resolver_cuentas(cli, None) == ("1", "2", "3")
    with pytest.raises(HTTPException) as e:
        ext_auth.resolver_cuentas(cli, "2,9")
    assert e.value.status_code == 403


# ── 4. revocar mata los tokens ya emitidos ───────────────────────────────────
def test_revocar_la_key_mata_el_token_ya_emitido(client, monkeypatch):
    """El token dice con qué key nació (`kid`) y esa key se re-valida siempre.

    Es lo que permite revocar al instante SIN una tabla de tokens: si esto se
    rompe, una key revocada seguiría sirviendo datos hasta que venza el token.
    """
    _montar_cliente(monkeypatch, key_viva=False)
    r = client.get("/v1/cuentas", headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "credencial_revocada"


def test_token_sin_kid_es_invalido(client, monkeypatch):
    """Sin `kid` no hay forma de revocar → el token no se acepta."""
    _montar_cliente(monkeypatch)
    tok = jwt.encode({"iss": config.EXT_ISSUER, "sub": "cli_test", "exp": 9_999_999_999},
                     _SECRET, algorithm="HS256")
    r = client.get("/v1/cuentas", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401


# ── 5. aranceles: sólo si está habilitado ────────────────────────────────────
@pytest.mark.parametrize("habilitado", [True, False])
def test_el_arancel_viaja_solo_si_el_cliente_lo_tiene(client, monkeypatch, habilitado):
    _montar_cliente(monkeypatch, aranceles=habilitado)
    visto = {}

    def _fake(**kw):
        visto.update(kw)
        return {"operaciones": [], "paginacion": {
            "limit": 500, "devueltas": 0, "hay_mas": False, "siguiente_cursor": None}}

    monkeypatch.setattr(lectura, "operaciones", _fake)
    client.get("/v1/operaciones", headers={"Authorization": f"Bearer {_token()}"})
    assert visto["incluir_aranceles"] is habilitado


def test_fila_sin_permiso_no_trae_arancel():
    fila = {"boleto": "B1", "fecha": "2026-08-01", "id_cuenta": "10452",
            "cuenta_nombre": "PEPITO", "ticker": "AL30", "tipo_operacion": "C",
            "operacion": "Compra", "cantidad": decimal.Decimal("100"),
            "bruto": decimal.Decimal("1234.56"), "moneda": "ARS", "mercado": "BYMA",
            "tasa": None, "mep": decimal.Decimal("1000"),
            "anulado": False, "actualizado_en": None,
            "arancel": decimal.Decimal("9.99")}
    assert "arancel" not in lectura._fila(fila, con_arancel=False)
    con = lectura._fila(fila, con_arancel=True)
    assert con["arancel"] == 9.99 and con["arancel_moneda"] == "ARS"


# ── 6. la mesa no se toca ────────────────────────────────────────────────────
def test_el_lector_externo_usa_el_predicado_de_la_mesa():
    """Si alguien copia el predicado en vez de reusarlo, el accionista y la mesa
    pueden empezar a decir números distintos sin que falle nada (REGLA #9)."""
    import inspect

    fuente = inspect.getsource(lectura)
    assert "_ops_where" in fuente
    assert "from api.services.operaciones_sql import _ops_where" in fuente


def test_los_anulados_no_salen_nunca():
    """El predicado compartido filtra `anulado_en IS NULL` y la API externa NO
    tiene forma de pedir lo contrario: no existe parámetro que abra esa puerta."""
    import inspect

    from api.services.operaciones_sql import _ops_where

    where, _ = _ops_where(arancel=True, scope=("10452",))
    assert "anulado_en IS NULL" in where
    # y nada en la API externa puede desactivarlo
    assert "anulado" not in inspect.getsource(lectura.operaciones)


# ── 7. montos como texto decimal ─────────────────────────────────────────────
def test_los_montos_viajan_como_numero():
    """Decisión del user: un número es un número. `null` sigue siendo `null` —
    lo que NO puede pasar es que un dato ausente se convierta en 0."""
    v = lectura._num(decimal.Decimal("1234.56"))
    assert isinstance(v, float) and v == 1234.56
    assert lectura._num(None) is None


def test_no_se_expone_es_cierre():
    """`es_cierre` es una marca NUESTRA (`"CIERRE" in tipo_operacion`,
    operaciones_informes.py:313). El dato ya viaja en `tipo_operacion`; el
    booleano interno no sale del sistema."""
    fila = {"boleto": "B1", "fecha": "2026-08-01", "id_cuenta": "10452",
            "cuenta_nombre": "PEPITO", "ticker": "AL30",
            "tipo_operacion": "Caución CIERRE", "operacion": "Compra",
            "cantidad": None, "bruto": None, "moneda": "ARS", "mercado": "BYMA",
            "tasa": None, "mep": None, "anulado": False,
            "actualizado_en": None, "arancel": None}
    salida = lectura._fila(fila, con_arancel=True)
    assert "es_cierre" not in salida
    assert "etapa" not in salida
    assert "segmento" not in salida and "nivel_3" not in salida
    assert salida["tipo_operacion"] == "Caución CIERRE"   # la info sigue estando


def test_el_titulo_se_identifica_solo_por_ticker():
    """El título viaja como TICKER y nada más: el string interno de la unidad de
    Aunesa no sale del sistema. Un ticker vacío viaja como `null` — nunca como
    string vacío ni como un descriptivo de repuesto."""
    base = {"boleto": "B1", "fecha": "2026-08-01", "id_cuenta": "10452",
            "cuenta_nombre": "PEPITO", "tipo_operacion": "Contado",
            "operacion": "Compra", "cantidad": None, "bruto": None,
            "moneda": "ARS", "mercado": "BYMA", "tasa": None, "mep": None,
            "anulado": False, "actualizado_en": None}
    con = lectura._fila({**base, "ticker": "AL30"}, False)
    assert con["ticker"] == "AL30"
    assert "descripcion" not in con and "instrumento" not in con

    sin = lectura._fila({**base, "ticker": None}, False)
    assert sin["ticker"] is None


def test_cursor_va_y_vuelve():
    c = lectura._cursor_encode(["2026-08-26T12:00:00Z", "4210"])
    assert lectura._cursor_decode(c) == ["2026-08-26T12:00:00Z", "4210"]
    with pytest.raises(ValueError):
        lectura._partes_cursor("no-es-base64-valido!!", 2)
