"""Tests unitarios del cliente BYMA (sin red real).

Cubren:
- OAuth2 token fetch + cache + expiración + refresh forzado.
- _get_json: éxito, 401 con retry, 429, otros códigos.
- Paginación automática con iter_pages.
- Errores de configuración (env vars faltantes).

No hace requests reales — todo va vía monkeypatch de core.byma.requests.
"""
from __future__ import annotations

import base64
import time
from unittest.mock import MagicMock

import pytest

import core.byma as byma


@pytest.fixture(autouse=True)
def _reset_client_state(monkeypatch):
    """Limpia cache de token y rate limit antes de cada test."""
    byma._token_cache["access_token"] = None
    byma._token_cache["expires_at"] = 0.0
    byma._token_cache["scope"] = None
    byma._calls_ts.clear()
    # Credenciales válidas por default en tests (salvo que se sobreescriba)
    monkeypatch.setattr(byma, "BYMA_CLIENT_ID",     "test-client-id")
    monkeypatch.setattr(byma, "BYMA_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(byma, "BYMA_TOKEN_URL",     "https://fake/token")
    monkeypatch.setattr(byma, "BYMA_BASE_URL",      "https://fake/v1")


def _mk_response(status: int, json_body=None, content: bytes = b"", text: str = ""):
    r = MagicMock()
    r.status_code = status
    if json_body is not None:
        r.json.return_value = json_body
    else:
        r.json.side_effect = ValueError("not json")
    r.text = text or (str(json_body) if json_body is not None else "")
    r.content = content
    return r


# ─────────────────────────────────────────────────────────────────────────────
# _ensure_configured
# ─────────────────────────────────────────────────────────────────────────────


def test_ensure_configured_raises_cuando_faltan_credenciales(monkeypatch):
    monkeypatch.setattr(byma, "BYMA_CLIENT_ID", "")
    with pytest.raises(byma.BymaNotConfigured) as exc:
        byma._ensure_configured()
    assert "BYMA_CLIENT_ID" in str(exc.value)


def test_ensure_configured_ok_cuando_todo_presente():
    # Las credenciales las setea el fixture _reset_client_state
    byma._ensure_configured()  # no raise


# ─────────────────────────────────────────────────────────────────────────────
# OAuth2 token
# ─────────────────────────────────────────────────────────────────────────────


def test_get_access_token_primera_llamada_hace_request(monkeypatch):
    mock_post = MagicMock(return_value=_mk_response(
        200, {"access_token": "tok-abc", "expires_in": 3600, "scope": "x"}
    ))
    monkeypatch.setattr(byma.requests, "post", mock_post)

    tok = byma.get_access_token()
    assert tok == "tok-abc"
    assert mock_post.call_count == 1

    # Header Basic Auth correcto
    _, kwargs = mock_post.call_args
    auth_header = kwargs["headers"]["Authorization"]
    assert auth_header.startswith("Basic ")
    decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode()
    assert decoded == "test-client-id:test-secret"

    # Body correcto
    assert kwargs["data"]["grant_type"] == "client_credentials"
    assert kwargs["data"]["scope"] == "bymaPrimariasPlacements.read"


def test_get_access_token_reusa_cache_si_no_expiró(monkeypatch):
    mock_post = MagicMock(return_value=_mk_response(
        200, {"access_token": "tok-xyz", "expires_in": 3600}
    ))
    monkeypatch.setattr(byma.requests, "post", mock_post)

    t1 = byma.get_access_token()
    t2 = byma.get_access_token()
    t3 = byma.get_access_token()
    assert t1 == t2 == t3 == "tok-xyz"
    assert mock_post.call_count == 1  # cache hit en 2da y 3ra


def test_get_access_token_refresh_si_expiró(monkeypatch):
    mock_post = MagicMock(side_effect=[
        _mk_response(200, {"access_token": "tok-1", "expires_in": 3600}),
        _mk_response(200, {"access_token": "tok-2", "expires_in": 3600}),
    ])
    monkeypatch.setattr(byma.requests, "post", mock_post)

    assert byma.get_access_token() == "tok-1"
    # Forzamos expiración manual
    byma._token_cache["expires_at"] = time.time() - 1
    assert byma.get_access_token() == "tok-2"
    assert mock_post.call_count == 2


def test_get_access_token_error_status(monkeypatch):
    monkeypatch.setattr(
        byma.requests, "post",
        MagicMock(return_value=_mk_response(401, text="invalid_client")),
    )
    with pytest.raises(byma.BymaAuthError) as exc:
        byma.get_access_token()
    assert "401" in str(exc.value)


def test_get_access_token_sin_access_token_en_respuesta(monkeypatch):
    monkeypatch.setattr(
        byma.requests, "post",
        MagicMock(return_value=_mk_response(200, {"token_type": "bearer"})),  # falta access_token
    )
    with pytest.raises(byma.BymaAuthError) as exc:
        byma.get_access_token()
    assert "access_token" in str(exc.value).lower()


def test_invalidate_token_fuerza_refresh(monkeypatch):
    mock_post = MagicMock(side_effect=[
        _mk_response(200, {"access_token": "tok-A", "expires_in": 3600}),
        _mk_response(200, {"access_token": "tok-B", "expires_in": 3600}),
    ])
    monkeypatch.setattr(byma.requests, "post", mock_post)

    assert byma.get_access_token() == "tok-A"
    byma.invalidate_token()
    assert byma.get_access_token() == "tok-B"


# ─────────────────────────────────────────────────────────────────────────────
# _get_json — happy path + errores
# ─────────────────────────────────────────────────────────────────────────────


def test_get_json_usa_bearer_header(monkeypatch):
    monkeypatch.setattr(
        byma.requests, "post",
        MagicMock(return_value=_mk_response(200, {"access_token": "tok-1", "expires_in": 3600})),
    )
    mock_get = MagicMock(return_value=_mk_response(200, {"ok": True}))
    monkeypatch.setattr(byma.requests, "get", mock_get)

    result = byma._get_json("/foo")
    assert result == {"ok": True}
    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer tok-1"


def test_get_json_retry_on_401(monkeypatch):
    monkeypatch.setattr(
        byma.requests, "post",
        MagicMock(side_effect=[
            _mk_response(200, {"access_token": "tok-old", "expires_in": 3600}),
            _mk_response(200, {"access_token": "tok-new", "expires_in": 3600}),
        ]),
    )
    # 1er GET: 401 → fuerza refresh del token. 2do GET: 200.
    monkeypatch.setattr(
        byma.requests, "get",
        MagicMock(side_effect=[
            _mk_response(401, text="expired"),
            _mk_response(200, {"result": []}),
        ]),
    )
    result = byma._get_json("/bar")
    assert result == {"result": []}


def test_get_json_no_retry_on_401_si_ya_reintentó(monkeypatch):
    monkeypatch.setattr(
        byma.requests, "post",
        MagicMock(return_value=_mk_response(200, {"access_token": "tok", "expires_in": 3600})),
    )
    monkeypatch.setattr(
        byma.requests, "get",
        MagicMock(return_value=_mk_response(401, text="still expired")),
    )
    with pytest.raises(byma.BymaError) as exc:
        byma._get_json("/bar")
    assert "401" in str(exc.value)


def test_get_json_429(monkeypatch):
    monkeypatch.setattr(
        byma.requests, "post",
        MagicMock(return_value=_mk_response(200, {"access_token": "tok", "expires_in": 3600})),
    )
    monkeypatch.setattr(
        byma.requests, "get",
        MagicMock(return_value=_mk_response(429, text="rate limit")),
    )
    with pytest.raises(byma.BymaRateLimitError):
        byma._get_json("/bar")


def test_get_json_respuesta_no_json(monkeypatch):
    monkeypatch.setattr(
        byma.requests, "post",
        MagicMock(return_value=_mk_response(200, {"access_token": "tok", "expires_in": 3600})),
    )
    monkeypatch.setattr(
        byma.requests, "get",
        MagicMock(return_value=_mk_response(200, text="<html>not json</html>")),
    )
    with pytest.raises(byma.BymaError) as exc:
        byma._get_json("/bar")
    assert "no-JSON" in str(exc.value) or "no-json" in str(exc.value).lower()


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints públicos (wrapping correcto)
# ─────────────────────────────────────────────────────────────────────────────


def test_get_underwriters_llama_path_correcto(monkeypatch):
    monkeypatch.setattr(
        byma.requests, "post",
        MagicMock(return_value=_mk_response(200, {"access_token": "tok", "expires_in": 3600})),
    )
    mock_get = MagicMock(return_value=_mk_response(200, {"result": []}))
    monkeypatch.setattr(byma.requests, "get", mock_get)

    byma.get_underwriters(page=2, size=25)

    _, kwargs = mock_get.call_args
    assert kwargs["params"] == {"page": 2, "size": 25}
    # URL completa incluye /underwriters.json
    args, _ = mock_get.call_args
    assert args[0].endswith("/underwriters.json")


def test_get_historical_placements_pasa_extra_filters(monkeypatch):
    monkeypatch.setattr(
        byma.requests, "post",
        MagicMock(return_value=_mk_response(200, {"access_token": "tok", "expires_in": 3600})),
    )
    mock_get = MagicMock(return_value=_mk_response(200, {"result": []}))
    monkeypatch.setattr(byma.requests, "get", mock_get)

    byma.get_historical_placements(page=0, size=10, issuerId=42, placementType="BOND")

    _, kwargs = mock_get.call_args
    assert kwargs["params"]["page"] == 0
    assert kwargs["params"]["size"] == 10
    assert kwargs["params"]["issuerId"] == 42
    assert kwargs["params"]["placementType"] == "BOND"


def test_get_document_content_devuelve_bytes(monkeypatch):
    monkeypatch.setattr(
        byma.requests, "post",
        MagicMock(return_value=_mk_response(200, {"access_token": "tok", "expires_in": 3600})),
    )
    monkeypatch.setattr(
        byma.requests, "get",
        MagicMock(return_value=_mk_response(200, content=b"%PDF-1.4 ...")),
    )
    out = byma.get_document_content(file_id=123, placement_id=456)
    assert out == b"%PDF-1.4 ..."


# ─────────────────────────────────────────────────────────────────────────────
# iter_pages — paginación automática
# ─────────────────────────────────────────────────────────────────────────────


def test_iter_pages_recorre_todas_las_paginas():
    """Simula endpoint paginado con 3 páginas de 2 items cada una."""
    call_count = {"n": 0}

    def fake_fn(page: int, size: int):
        call_count["n"] += 1
        if page == 0:
            return {"meta": {"page": 0, "totalPages": 3}, "result": [{"id": 1}, {"id": 2}]}
        if page == 1:
            return {"meta": {"page": 1, "totalPages": 3}, "result": [{"id": 3}, {"id": 4}]}
        return {"meta": {"page": 2, "totalPages": 3}, "result": [{"id": 5}, {"id": 6}]}

    items = list(byma.iter_pages(fake_fn, page_size=2))
    assert [i["id"] for i in items] == [1, 2, 3, 4, 5, 6]
    assert call_count["n"] == 3


def test_iter_pages_corta_si_no_hay_meta():
    """Si el endpoint no devuelve totalPages, itera solo la primera página."""
    def fake_fn(page: int, size: int):
        return {"result": [{"id": 1}]}  # sin meta

    items = list(byma.iter_pages(fake_fn, page_size=10))
    assert items == [{"id": 1}]


def test_iter_pages_result_vacio():
    def fake_fn(page: int, size: int):
        return {"meta": {"page": 0, "totalPages": 1}, "result": []}

    items = list(byma.iter_pages(fake_fn))
    assert items == []


# ─────────────────────────────────────────────────────────────────────────────
# Rate limit interno
# ─────────────────────────────────────────────────────────────────────────────


def test_rate_limit_interno_bloquea_al_sobrepasar(monkeypatch):
    """Simulamos 30 calls previos y vemos que el próximo spera."""
    now = time.time()
    byma._calls_ts.extend([now - 30] * byma._MAX_CALLS_PER_MIN)

    sleep_calls = {"n": 0, "total": 0.0}

    def fake_sleep(s):
        sleep_calls["n"] += 1
        sleep_calls["total"] += s
        # También simulamos que el tiempo avanzó para evitar loops
        byma._calls_ts.clear()

    monkeypatch.setattr(byma.time, "sleep", fake_sleep)

    byma._wait_for_rate_limit()  # debería dormir
    assert sleep_calls["n"] == 1
    assert sleep_calls["total"] > 0
