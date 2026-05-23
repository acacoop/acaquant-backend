"""Test del fail-closed de auth al boot (EXT-AUTH1, api/main.py).

Si ENV=prod y falta API_KEY, la API NO debe arrancar. En dev solo warn.
"""
from __future__ import annotations

import pytest

import api.main as m


def test_prod_sin_api_key_aborta_boot(monkeypatch):
    monkeypatch.setattr(m, "ENV", "prod")
    monkeypatch.setattr(m, "API_KEY", "")
    with pytest.raises(RuntimeError, match="EXT-AUTH1"):
        m._validar_postura_auth()


def test_prod_con_api_key_arranca(monkeypatch):
    monkeypatch.setattr(m, "ENV", "prod")
    monkeypatch.setattr(m, "API_KEY", "secret")
    monkeypatch.setattr(m, "CF_ACCESS_TEAM", "team")
    monkeypatch.setattr(m, "CF_ACCESS_AUD", "aud")
    m._validar_postura_auth()  # no raise


def test_dev_sin_api_key_no_aborta(monkeypatch):
    # Modo dev: permisivo, no rompe local.
    monkeypatch.setattr(m, "ENV", "dev")
    monkeypatch.setattr(m, "API_KEY", "")
    m._validar_postura_auth()  # no raise
