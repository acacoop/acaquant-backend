"""Tests del gateway de IA (core/ai.py) — solo las partes puras, sin red ni DB.

Congelan el contrato: sin key → None (sin tocar red/DB), resolución de modelo
por tier con overrides por env, y fallback de tareas desconocidas.
"""
from __future__ import annotations

from core import ai


def test_sin_key_devuelve_none_sin_tocar_nada(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert ai.completar("smoke", system="x", user="y") is None


def test_tarea_desconocida_cae_a_default_flash():
    cfg = ai._config("tarea_que_no_existe")
    assert cfg["tier"] == "flash"
    assert cfg["max_tokens"] > 0 and cfg["timeout_s"] > 0


def test_modelo_por_tier(monkeypatch):
    monkeypatch.delenv("AI_MODEL_FLASH", raising=False)
    monkeypatch.delenv("AI_MODEL_PRO", raising=False)
    assert ai._modelo({"tier": "flash"}) == "deepseek-v4-flash"
    assert ai._modelo({"tier": "pro"}) == "deepseek-v4-pro"


def test_override_global_por_env(monkeypatch):
    monkeypatch.setenv("AI_MODEL_FLASH", "otro-modelo")
    assert ai._modelo({"tier": "flash"}) == "otro-modelo"


def test_override_por_tarea_gana(monkeypatch):
    monkeypatch.setenv("AI_MODEL_FLASH", "modelo-global")
    monkeypatch.setenv("AI_RESUMEN_MODEL", "modelo-de-la-tarea")
    cfg = ai._config("controles_resumen")
    assert ai._modelo(cfg) == "modelo-de-la-tarea"
