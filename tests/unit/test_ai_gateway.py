"""Tests del gateway de IA (core/ai.py) — solo las partes puras, sin red ni DB.

Congelan el contrato: sin key → None (sin tocar red/DB), resolución de modelo
por tier con overrides por env, fallback de tareas desconocidas, y precedencia
de presupuestos (ia.config > env > default) + sus validaciones.
"""
from __future__ import annotations

import pytest

from core import ai


def test_sin_key_devuelve_none_sin_tocar_nada(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert ai.completar("smoke", system="x", user="y") is None
    assert ai.completar_con_traza("smoke", system="x", user="y") == (None, None)


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


def test_presupuestos_precedencia_db_env_default(monkeypatch):
    # 1) la tabla ia.config gana
    monkeypatch.setattr(
        ai, "_config_db",
        lambda: {"budget_dia_global": 500_000, "budget_dia_usuario": 100_000,
                 "budget_dia_usuario:admin@x.com": 400_000},
    )
    assert ai.presupuesto_dia_global() == 500_000
    assert ai.presupuesto_dia_usuario() == 100_000
    # excepción personal pisa el general SOLO para ese email
    assert ai.presupuesto_dia_usuario("admin@x.com") == 400_000
    assert ai.presupuesto_dia_usuario("otro@x.com") == 100_000
    # 2) sin fila en la tabla → env var
    monkeypatch.setattr(ai, "_config_db", dict)
    monkeypatch.setenv("AI_BUDGET_TOKENS_DIA", "777000")
    assert ai.presupuesto_dia_global() == 777_000
    # 3) sin nada → defaults del código
    monkeypatch.delenv("AI_BUDGET_TOKENS_DIA", raising=False)
    monkeypatch.delenv("AI_BUDGET_TOKENS_DIA_USUARIO", raising=False)
    assert ai.presupuesto_dia_global() == 2_000_000
    assert ai.presupuesto_dia_usuario() == 1_000_000


def test_set_presupuestos_valida_antes_de_tocar_db(monkeypatch):
    from api.services import ia_obs

    monkeypatch.setattr("core.ai.presupuesto_dia_global", lambda: 2_000_000)
    monkeypatch.setattr("core.ai.presupuesto_dia_usuario", lambda: 1_000_000)
    with pytest.raises(ValueError):
        ia_obs.set_presupuestos(-5, None, actor="a@b.com")  # negativo
    with pytest.raises(ValueError):
        ia_obs.set_presupuestos(None, 0, actor="a@b.com")  # cero
    with pytest.raises(ValueError):
        # usuario (3M) > global vigente (2M) — el global es techo duro
        ia_obs.set_presupuestos(None, 3_000_000, actor="a@b.com")


def test_set_presupuesto_usuario_valida(monkeypatch):
    from api.services import ia_obs

    monkeypatch.setattr("core.ai.presupuesto_dia_global", lambda: 2_000_000)
    with pytest.raises(ValueError):
        ia_obs.set_presupuesto_usuario("no-es-mail", 100, actor="a@b.com")
    with pytest.raises(ValueError):
        ia_obs.set_presupuesto_usuario("x@y.com", 0, actor="a@b.com")
    with pytest.raises(ValueError):
        # excepción personal tampoco puede superar el global
        ia_obs.set_presupuesto_usuario("x@y.com", 3_000_000, actor="a@b.com")
