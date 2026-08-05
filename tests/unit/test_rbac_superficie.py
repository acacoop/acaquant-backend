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


def test_portal_invitado_no_llega_al_negocio():
    """Default-deny real del portal www: la allowlist de paths (REGLA #8).

    El check de `require_module` sólo corre en routers gateados; el middleware
    `_guard_portal_invitado` cierra el resto. Este test congela que ningún path
    del NEGOCIO de la mesa entre a la allowlist, ni siquiera por un prefijo que
    tape de más.
    """
    from api.auth import path_permitido_invitado

    negocio = [
        "/api/portfolio/posiciones", "/api/operaciones/ops", "/api/manager/roles",
        "/api/mesa-dinero/ops", "/api/back-office/senebis/ops", "/api/cuentas",
        "/api/ordenes/live", "/api/operar/mep", "/api/risk/limites",
        "/api/trading/pivots", "/api/mm/book", "/api/ingest/eikon",
    ]
    filtrados = [p for p in negocio if path_permitido_invitado(p)]
    assert not filtrados, f"el portal invitado alcanzaría negocio (REGLA #8): {filtrados}"


def test_toda_ruta_alcanzable_por_invitado_es_de_mercado():
    """Una ruta nueva bajo un prefijo de la allowlist entra sola al portal www.

    Este test la hace visible: si aparece algo que no sea mercado/research/IA,
    o se saca de ahí, o se agrega a la excepción a propósito.
    """
    from api.auth import GUEST_PATH_PREFIXES

    permitidos = {
        "/api/health", "/api/me", "/api/analitica", "/api/cotizaciones",
        "/api/derivados", "/api/titulos", "/api/market", "/api/news",
        "/api/scanner", "/api/research1816", "/api/research-bcra",
        "/api/research-fred", "/api/research-docs", "/api/ia",
    }
    assert set(GUEST_PATH_PREFIXES) == permitidos, (
        "cambió la superficie del portal invitado — es una decisión de "
        "SEGURIDAD (REGLA #8), actualizá este test a conciencia"
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


# ── 5. BOLA — endpoints con id_cuenta verifican pertenencia ──────────────────
# Bug real (auditoría 2026-08-03): estos aceptaban cualquier `id_cuenta` y
# devolvían la cartera / las operaciones / los cobros de esa cuenta sin mirar
# si el usuario tenía acceso a ella.

@pytest.mark.parametrize("path", [
    "/api/operaciones/comercial/portafolio",
    "/api/operaciones/comercial/operaciones",
    "/api/operaciones/comercial/cobros-futuros/cliente",
    "/api/back-office/acreencias/cliente",
])
def test_endpoints_con_id_cuenta_verifican_pertenencia(path):
    encontrada = False
    for p, _m, deps in _rutas():
        if p != path:
            continue
        encontrada = True
        assert "verificar_id_cuenta" in deps, (
            f"{path} acepta id_cuenta sin verificar que la cuenta esté en el "
            "grupo del usuario (BOLA/IDOR)"
        )
    assert encontrada, f"{path} no existe — si se renombró, actualizá el test"


def test_serie_comercial_verifica_id_cuenta_opcional():
    """`/comercial/serie` tiene id_cuenta OPCIONAL: usa la variante que no lo

    vuelve obligatorio, pero que igual valida cuando viene.
    """
    for p, _m, deps in _rutas():
        if p == "/api/operaciones/comercial/serie":
            assert "verificar_id_cuenta_opcional" in deps
            return
    pytest.fail("/api/operaciones/comercial/serie no existe")


# ── 6. Higiene de configuración ──────────────────────────────────────────────


def test_swagger_cerrado_en_prod(monkeypatch):
    """/docs, /redoc y /openapi.json publican el mapa entero de la API.

    En prod tienen que estar apagados (los cubre CF Access, pero es una sola
    capa). Se re-importa `api.main` con ENV=prod para ver la app real.
    """
    import importlib

    import api.main as m

    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setattr("config.ENV", "prod", raising=False)
    recargado = importlib.reload(m)
    try:
        assert recargado._DOCS_ABIERTOS is False, (
            "con ENV=prod los docs deben quedar cerrados"
        )
        paths = {getattr(r, "path", None) for r in recargado.app.routes}
        assert "/docs" not in paths
        assert "/openapi.json" not in paths
    finally:
        monkeypatch.undo()
        importlib.reload(m)


def test_rate_limit_tiene_default_global():
    """Sin default, sólo un puñado de endpoints tenía techo y cualquier

    cliente en loop podía saturar el pool de Postgres de toda la mesa.
    """
    from api.ratelimit import _DEFAULT_LIMITS, limiter

    assert _DEFAULT_LIMITS, "default_limits vacío: la API no tiene techo global"
    assert limiter._default_limits, "el Limiter se construyó sin default_limits"


def test_rate_limit_keyea_por_usuario_real():
    """El SSR pega con service token: sin mirar `x-acaquant-user-email` todos

    los usuarios comparten un único bucket y el límite por usuario no existe.
    """
    from starlette.datastructures import Headers

    from api.ratelimit import _key_by_user

    class _Req:
        def __init__(self, h):
            self.headers = Headers(h)
            self.client = None

    assert _key_by_user(_Req({"x-acaquant-user-email": "Ana@Acme.com"})) == "ana@acme.com"
    # el email del usuario gana sobre el JWT del service token
    key = _key_by_user(_Req({
        "x-acaquant-user-email": "ana@acme.com",
        "cf-access-jwt-assertion": "xxx.yyy.zzz",
    }))
    assert key == "ana@acme.com"
