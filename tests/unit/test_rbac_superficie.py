"""Guardrails de la SUPERFICIE HTTP — auditoría de seguridad 2026-08-03.

Complementa `test_rbac.py` (que congela la MATRIZ) congelando el **wiring**:
recorre `app.routes` reales y verifica que cada ruta tenga el gate que le
corresponde. Un router nuevo montado sin dependency, o un endpoint al que le
sacan el `require_admin`, rompe acá — no en producción.

Por qué a nivel `app.routes` y no leyendo el código: las dependencies se
resuelven por composición (montaje en `api/main.py` + `dependencies=` del
sub-router + las del decorador). Sólo el árbol ya armado dice la verdad sobre
qué gate aplica realmente a una ruta.

Hallazgos que congela (los tres primeros son bugs que existieron y se
arreglaron en este mismo commit):
  - `GET /api/ia/observabilidad|presupuesto|saldo` sin `require_admin`
    (exponían las conversaciones de IA de toda la mesa al portal invitado).
  - Escrituras en routers de mercado sin `require_no_invitado` (REGLA #8).
  - Rutas de negocio montadas sin bearer.
"""
from __future__ import annotations

import pytest

from api.main import app

# ── helpers ──────────────────────────────────────────────────────────────────


def _dep_names(route) -> set[str]:
    """Nombres de TODAS las dependencies resueltas para la ruta (recursivo).

    `require_module("x")` genera funciones con `__name__` = `require_module_x`
    (ver api/auth.py), así que el nombre alcanza para identificar el gate.
    """
    dep = getattr(route, "dependant", None)
    if dep is None:
        return set()
    out: set[str] = set()
    stack = [dep]
    while stack:
        d = stack.pop()
        call = getattr(d, "call", None)
        if call is not None:
            out.add(getattr(call, "__name__", ""))
        stack.extend(getattr(d, "dependencies", []) or [])
    return out


def _rutas():
    """(path, methods, deps) de cada ruta de la app, sin HEAD/OPTIONS."""
    for r in app.routes:
        path = getattr(r, "path", None)
        if path is None:
            continue
        methods = {m for m in (getattr(r, "methods", None) or set())
                   if m not in ("HEAD", "OPTIONS")}
        if not methods:
            continue
        yield path, methods, _dep_names(r)


def _tiene_gate_modulo(deps: set[str]) -> bool:
    return any(
        d.startswith(("require_module_", "require_any_module_"))
        or d in ("require_admin", "require_control_comercial")
        for d in deps
    )


# Rutas sin bearer a propósito. Cada una con su razón — agregar algo acá es una
# decisión de SEGURIDAD, no un trámite para hacer pasar el test.
_SIN_BEARER_OK = {
    "/api/health",        # liveness probe
    "/api/me",            # identidad propia; no devuelve datos de negocio
    "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect",  # swagger
}

# `/api/ingest/*` tiene auth PROPIA (X-Ingest-Token, fail-closed sin la env var
# → 503). No lleva verify_api_key a propósito: lo llama la PC de oficina.
_PREFIJO_AUTH_PROPIA = "/api/ingest/"


# ── 1. Nada de negocio sin bearer ────────────────────────────────────────────


def test_toda_ruta_api_exige_bearer_salvo_excepciones_declaradas():
    faltantes = [
        (p, sorted(m)) for p, m, deps in _rutas()
        if p.startswith("/api/")
        and "verify_api_key" not in deps
        and p not in _SIN_BEARER_OK
        and not p.startswith(_PREFIJO_AUTH_PROPIA)
    ]
    assert not faltantes, (
        "Rutas /api/* sin verify_api_key. Si es intencional, sumala a "
        f"_SIN_BEARER_OK con su justificación: {faltantes}"
    )


def test_ingest_usa_su_token_dedicado():
    """/api/ingest/* no lleva bearer pero SIEMPRE lleva verify_ingest_token."""
    sin_token = [
        (p, sorted(m)) for p, m, deps in _rutas()
        if p.startswith(_PREFIJO_AUTH_PROPIA) and "verify_ingest_token" not in deps
    ]
    assert not sin_token, f"rutas de ingesta sin verify_ingest_token: {sin_token}"


# ── 2. El panel de IA es admin-only ──────────────────────────────────────────
# Bug real (auditoría 2026-08-03): estos GET colgaban solo de require_module("ia").
# Como `ia` ∈ INVITADO_MODULES (core/roles.py), el portal www podía leer
# `detalle`/`respuesta` de ia.trazas = las conversaciones de toda la mesa.

@pytest.mark.parametrize("path", [
    "/api/ia/observabilidad",
    "/api/ia/presupuesto",
    "/api/ia/saldo",
])
def test_panel_ia_es_admin_only(path):
    encontrada = False
    for p, _m, deps in _rutas():
        if p != path:
            continue
        encontrada = True
        assert "require_admin" in deps, (
            f"{path} sin require_admin: expone trazas/config del gateway de IA "
            "a cualquier rol con el módulo `ia` y al portal INVITADO (REGLA #8)"
        )
    assert encontrada, f"{path} no existe — si se renombró, actualizá el test"


# ── 3. REGLA #8 — el invitado nunca escribe ──────────────────────────────────


def test_escrituras_sin_gate_de_modulo_estan_declaradas():
    """POST/PATCH/PUT/DELETE sin gate de módulo = alcanzable por el invitado.

    Sólo se admiten las que NO tocan la base (cálculo puro sobre el body).
    """
    permitidas = {
        "/api/analitica/estrategia-historico",      # backtest en memoria
        "/api/derivados/agro/estrategia/simular",   # simulador, no persiste
    }
    escrituras = {"POST", "PATCH", "PUT", "DELETE"}
    abiertas = [
        (p, sorted(m & escrituras)) for p, m, deps in _rutas()
        if (m & escrituras) and p.startswith("/api/")
        and not _tiene_gate_modulo(deps)
        and "require_no_invitado" not in deps
        and p not in permitidas
        and not p.startswith(_PREFIJO_AUTH_PROPIA)
    ]
    assert not abiertas, (
        "Escrituras sin gate de módulo ni require_no_invitado — el portal "
        f"invitado podría ejecutarlas (REGLA #8): {abiertas}"
    )


def test_escrituras_de_agro_llevan_require_no_invitado():
    """`agro` ∈ INVITADO_MODULES: el guest VE la pizarra pero NUNCA la escribe.

    El gate de módulo no alcanza — sólo `require_no_invitado` lo frena.
    """
    escrituras = {"POST", "PATCH", "PUT", "DELETE"}
    sin_guard = [
        (p, sorted(m & escrituras)) for p, m, deps in _rutas()
        if (m & escrituras)
        and p.startswith("/api/derivados/agro")
        and "require_no_invitado" not in deps
        and "require_admin" not in deps
        and "simular" not in p  # cálculo puro, no persiste
    ]
    assert not sin_guard, (
        f"escrituras de agro sin require_no_invitado (REGLA #8): {sin_guard}"
    )


# ── 4. Todo /api/manager/* gateado ───────────────────────────────────────────


def test_todo_manager_tiene_gate():
    """Un sub-router nuevo sin `dependencies=` en manager/__init__.py

    quedaría accesible a cualquier autenticado. La base `_MANAGER_BASE` de
    api/main.py debería atajarlo; este test verifica que así sea.
    """
    sin_gate = [
        (p, sorted(m)) for p, m, deps in _rutas()
        if p.startswith("/api/manager") and not _tiene_gate_modulo(deps)
    ]
    assert not sin_gate, f"rutas de manager sin gate de módulo: {sin_gate}"
