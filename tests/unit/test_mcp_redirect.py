"""Test del allowlist de redirect_uri del DCR del MCP (anti open-redirect)."""
from __future__ import annotations

from api.mcp.oauth import _redirect_uri_permitido


def test_dominios_claude_permitidos():
    assert _redirect_uri_permitido("https://claude.ai/api/mcp/callback") is True
    assert _redirect_uri_permitido("https://claude.com/x") is True


def test_subdominios_permitidos():
    assert _redirect_uri_permitido("https://foo.claude.ai/cb") is True


def test_host_ajeno_rechazado():
    assert _redirect_uri_permitido("https://evil.com/cb") is False


def test_no_confunde_sufijo_falso():
    # claude.ai.evil.com NO debe matchear claude.ai.
    assert _redirect_uri_permitido("https://claude.ai.evil.com/cb") is False


def test_url_invalida_rechazada():
    assert _redirect_uri_permitido("not-a-url") is False
    assert _redirect_uri_permitido("") is False
