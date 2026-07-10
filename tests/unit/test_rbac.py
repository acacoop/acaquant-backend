"""Golden tests de RBAC (core/roles.py).

Congela la matriz de permisos crítica: que `operar`/`manager` queden
restringidos y que `has_access` falle cerrado ante módulos desconocidos o
identidades sin role. Si alguien afloja un gate por accidente, salta acá.
"""
from __future__ import annotations

import core.roles as roles
from core.roles import DEFAULT_MATRIX, MODULES, has_access

# ── DEFAULT_MATRIX: invariantes del bootstrap ────────────────────────────────

def test_admin_tiene_todos_los_modulos():
    assert DEFAULT_MATRIX["admin"] == MODULES


def test_sales_no_puede_operar():
    assert "operar" not in DEFAULT_MATRIX["sales"]


def test_trader_no_puede_operar_en_default():
    # operar quedó admin-only (decisión 2026-05-17).
    assert "operar" not in DEFAULT_MATRIX["trader"]


def test_manager_es_admin_only():
    assert "manager" not in DEFAULT_MATRIX["sales"]
    assert "manager" not in DEFAULT_MATRIX["trader"]
    assert "manager" in DEFAULT_MATRIX["admin"]


def test_sales_no_ve_datos_privados():
    # operaciones (contrapartes/flujo) y portfolios (AuM) no son para sales.
    assert "operaciones" not in DEFAULT_MATRIX["sales"]
    assert "portfolios" not in DEFAULT_MATRIX["sales"]


def test_matriz_solo_usa_modulos_canonicos():
    for role, mods in DEFAULT_MATRIX.items():
        for m in mods:
            assert m in MODULES, f"{role} tiene módulo no-canónico {m!r}"


# ── has_access: enforcement (con matriz/role mockeados) ──────────────────────

def _patch(monkeypatch, role: str, matrix: dict):
    monkeypatch.setattr(roles, "get_user_role", lambda email: role)
    monkeypatch.setattr(roles, "get_matrix", lambda: matrix)


def test_has_access_concede_modulo_del_role(monkeypatch):
    _patch(monkeypatch, "sales", {"sales": ("home", "renta-fija")})
    assert has_access("x@acaquant.com", "renta-fija") is True


def test_has_access_niega_modulo_fuera_del_role(monkeypatch):
    _patch(monkeypatch, "sales", {"sales": ("home", "renta-fija")})
    assert has_access("x@acaquant.com", "operar") is False


def test_has_access_modulo_desconocido_falla_cerrado(monkeypatch):
    _patch(monkeypatch, "admin", {"admin": MODULES})
    # Aunque sea admin, un módulo que no existe en MODULES → False (anti-typo).
    assert has_access("x@acaquant.com", "modulo_inexistente") is False


def test_has_access_role_sin_entrada_en_matriz_no_ve_nada(monkeypatch):
    # role "none" (anon / service token) no está en la matriz → 0 módulos.
    _patch(monkeypatch, "none", {"admin": MODULES})
    assert has_access("anon", "home") is False


# ── Módulo `ia` (QuantAI, docs/QUANTAI.md) ───────────────────────────────────

def test_ia_es_modulo_canonico():
    assert "ia" in MODULES


def test_ia_default_solo_admin():
    # Canary del rollout (2026-07-10): en el bootstrap solo admin lo tiene.
    for role, mods in DEFAULT_MATRIX.items():
        if role == "admin":
            assert "ia" in mods
        else:
            assert "ia" not in mods, f"{role} no debe tener `ia` por default"


def test_invitado_jamas_tiene_ia():
    # REGLA #8: el portal público jamás ve features de IA. INVITADO_MODULES es
    # la fuente única del gate del invitado (no depende de la matriz viva).
    assert "ia" not in roles.INVITADO_MODULES


def test_prefijo_api_ia_mapea_al_modulo_ia():
    from api.auth import get_module_for_path
    assert get_module_for_path("/api/ia/cualquier-endpoint-futuro") == "ia"
