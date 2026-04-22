"""Tests unitarios del cliente MAE (sin red real).

Cubre:
- _ensure_configured falla si no hay API key.
- _base_url elige prod/uat/fallback según MAE_ENV.
- get_repo arma request correcta con x-api-key header.
- Auth errors (401, 403) → MaeAuthError.
- 429 → MaeRateLimitError.
- Otros status → MaeError.
- Payload no-JSON → MaeError.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import core.mae as mae


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Reset rate-limit y fija creds válidas por default."""
    mae._calls_ts.clear()
    monkeypatch.setattr(mae, "MAE_API_KEY", "test-key-abc")
    monkeypatch.setattr(mae, "MAE_ENV",     "prod")


def _mk_response(status: int, json_body=None, text: str = ""):
    r = MagicMock()
    r.status_code = status
    if json_body is not None:
        r.json.return_value = json_body
    else:
        r.json.side_effect = ValueError("not json")
    r.text = text or (str(json_body) if json_body is not None else "")
    return r


# ─────────────────────────────────────────────────────────────────────────────
# Configuración
# ─────────────────────────────────────────────────────────────────────────────


def test_ensure_configured_requiere_api_key(monkeypatch):
    monkeypatch.setattr(mae, "MAE_API_KEY", "")
    with pytest.raises(mae.MaeNotConfigured):
        mae._ensure_configured()


def test_ensure_configured_ok_con_key():
    mae._ensure_configured()  # no raise (fixture setea la key)


def test_base_url_prod(monkeypatch):
    monkeypatch.setattr(mae, "MAE_ENV", "prod")
    assert mae._base_url() == "https://api.mae.com.ar"


def test_base_url_uat(monkeypatch):
    monkeypatch.setattr(mae, "MAE_ENV", "uat")
    assert mae._base_url() == "https://apiuat.mae.com.ar"


def test_base_url_env_desconocido_cae_a_prod(monkeypatch):
    monkeypatch.setattr(mae, "MAE_ENV", "sandbox")
    assert mae._base_url() == "https://api.mae.com.ar"


# ─────────────────────────────────────────────────────────────────────────────
# GET + auth header
# ─────────────────────────────────────────────────────────────────────────────


def test_get_repo_usa_x_api_key_header(monkeypatch):
    mock_get = MagicMock(return_value=_mk_response(200, []))
    monkeypatch.setattr(mae.requests, "get", mock_get)

    mae.get_repo(page=2)

    args, kwargs = mock_get.call_args
    assert args[0] == "https://api.mae.com.ar/api/v1/mercado/cotizaciones/repo"
    assert kwargs["headers"]["x-api-key"] == "test-key-abc"
    assert kwargs["headers"]["Accept"] == "application/json"
    assert kwargs["params"] == {"pageNumber": 2}


def test_get_repo_default_page_1(monkeypatch):
    mock_get = MagicMock(return_value=_mk_response(200, []))
    monkeypatch.setattr(mae.requests, "get", mock_get)

    mae.get_repo()

    _, kwargs = mock_get.call_args
    assert kwargs["params"] == {"pageNumber": 1}


def test_get_repo_devuelve_json(monkeypatch):
    monkeypatch.setattr(
        mae.requests, "get",
        MagicMock(return_value=_mk_response(200, [{"fecha": "2026-04-21", "plazo": "001"}])),
    )
    out = mae.get_repo()
    assert out == [{"fecha": "2026-04-21", "plazo": "001"}]


def test_iter_repo_pages_corta_en_vacia(monkeypatch):
    # Devuelve 2 items, 1 item, vacía → itera 2 páginas (la 3ra ya no yield)
    responses = iter([
        _mk_response(200, [{"fecha": "a"}, {"fecha": "b"}]),
        _mk_response(200, [{"fecha": "c"}]),
        _mk_response(200, []),
    ])
    monkeypatch.setattr(mae.requests, "get", MagicMock(side_effect=lambda *a, **k: next(responses)))

    pages = list(mae.iter_repo_pages(max_pages=10))
    # La vacía se yield también (lo corta dentro con len==0)
    assert len(pages) == 3
    assert len(pages[0]) == 2
    assert len(pages[1]) == 1
    assert pages[2] == []


# ─────────────────────────────────────────────────────────────────────────────
# Error handling
# ─────────────────────────────────────────────────────────────────────────────


def test_get_repo_401_raises_auth_error(monkeypatch):
    monkeypatch.setattr(
        mae.requests, "get",
        MagicMock(return_value=_mk_response(401, text="unauthorized")),
    )
    with pytest.raises(mae.MaeAuthError):
        mae.get_repo()


def test_get_repo_403_raises_auth_error(monkeypatch):
    monkeypatch.setattr(
        mae.requests, "get",
        MagicMock(return_value=_mk_response(403, text="forbidden")),
    )
    with pytest.raises(mae.MaeAuthError):
        mae.get_repo()


def test_get_repo_429_raises_rate_limit(monkeypatch):
    monkeypatch.setattr(
        mae.requests, "get",
        MagicMock(return_value=_mk_response(429, text="too many")),
    )
    with pytest.raises(mae.MaeRateLimitError):
        mae.get_repo()


def test_get_repo_500_raises_error(monkeypatch):
    monkeypatch.setattr(
        mae.requests, "get",
        MagicMock(return_value=_mk_response(500, text="internal")),
    )
    with pytest.raises(mae.MaeError) as exc:
        mae.get_repo()
    assert "500" in str(exc.value)


def test_get_repo_red_caida(monkeypatch):
    import requests as real_requests
    monkeypatch.setattr(
        mae.requests, "get",
        MagicMock(side_effect=real_requests.ConnectionError("timeout")),
    )
    with pytest.raises(mae.MaeError) as exc:
        mae.get_repo()
    assert "red falló" in str(exc.value).lower()


def test_get_repo_payload_no_json(monkeypatch):
    monkeypatch.setattr(
        mae.requests, "get",
        MagicMock(return_value=_mk_response(200, json_body=None, text="<html>")),
    )
    with pytest.raises(mae.MaeError) as exc:
        mae.get_repo()
    assert "no-JSON" in str(exc.value) or "no-json" in str(exc.value).lower()


def test_get_repo_sin_api_key(monkeypatch):
    monkeypatch.setattr(mae, "MAE_API_KEY", "")
    with pytest.raises(mae.MaeNotConfigured):
        mae.get_repo()
