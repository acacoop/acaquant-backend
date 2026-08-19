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
    # Excepción 2026-07-21: `invitado` lo tiene por decisión del user (con las
    # condiciones congeladas en test_invitado_ia_con_condiciones).
    for role, mods in DEFAULT_MATRIX.items():
        if role in ("admin", "invitado"):
            assert "ia" in mods
        else:
            assert "ia" not in mods, f"{role} no debe tener `ia` por default"


def test_al_invitado_solo_le_queda_el_BRIEFING_bajo_api_ia():
    """REGLA #8 — qué alcanza un invitado dentro de `/api/ia`.

    El módulo `ia` se le dio al invitado en 2026-07-21 **para los copilotos de
    mercado**. El 2026-08-19 los copilotos se dieron de baja, así que la
    pregunta se rehace: lo único no-admin que queda bajo ese prefijo es el
    BRIEFING (futuros US, dólar oficial, MEP/CCL), que **es** dato de mercado y
    por lo tanto es exactamente lo que el portal invitado existe para mostrar.

    Este test congela el otro lado: **todo lo demás de `/api/ia` es admin-only**.
    Es lo que impide que un endpoint nuevo del AV AGENT —que habla del estado
    interno del sistema— nazca alcanzable por el portal www sin que nadie lo note.
    """
    from api.main import app
    from api.routers import ia as router_ia

    assert "ia" in roles.INVITADO_MODULES
    abiertas = []
    for r in router_ia.router.routes:
        gates = [str(d.dependency) for d in getattr(r, "dependencies", [])]
        if not any("require_admin" in g for g in gates):
            abiertas.append(getattr(r, "path", "?"))
    assert abiertas == ["/api/ia/briefing"], (
        f"endpoints de /api/ia sin require_admin: {abiertas} — si alguno es "
        "para el invitado, decidilo explícito (REGLA #8: default-deny)")
    assert app is not None


def test_el_invitado_conserva_su_identidad_y_su_tope():
    """Cada invitado es `guest:<email>`: su propio tope diario, y jamás confundido
    con un interno."""
    from core import ai

    g = f"{roles.GUEST_PREFIX}cliente@externo.com"
    assert roles.es_invitado_id(g) and not roles.es_invitado_id("nico@acavalores.com")
    assert ai.presupuesto_dia_usuario(g) == 100_000


def test_prefijo_api_ia_mapea_al_modulo_ia():
    from api.auth import get_module_for_path
    assert get_module_for_path("/api/ia/cualquier-endpoint-futuro") == "ia"


def test_invitado_research_habilitado():
    # Decisión user 2026-07-21: los invitados son OTRO SECTOR de la MISMA
    # empresa (no terceros) → research completo habilitado (sin problema de
    # redistribución de licencias). El negocio de la mesa sigue excluido.
    assert "research" in roles.INVITADO_MODULES
    for privada in ("operaciones", "portfolios", "back-office", "manager", "trading"):
        assert privada not in roles.INVITADO_MODULES


# ── Los AVISOS del agente SÍ llegan a un no-admin (2026-08-19) ─────────────

def test_el_agente_es_admin_only_pero_lo_que_MANDA_llega_a_cualquiera():
    """Regla del user: *«el AV AGENT es solo para admin, no para el resto —
    aunque esto no quiere decir que no tenga el poder para mandar una alerta o
    notificación a otro user que no sea admin»*.

    Y ahí había un bug real: el endpoint para leer los avisos vivía bajo
    `/api/ia`, gateado por el módulo `ia` **que solo tienen admin e invitado**.
    O sea que el agente le podía escribir a un trader y el trader no lo veía
    nunca. Por eso los avisos viven en su propio router SIN gate de módulo.
    """
    from api.auth import get_module_for_path

    # El agente, admin-only por su prefijo.
    assert get_module_for_path("/api/ia/av-agent/skills") == "ia"
    assert "ia" not in roles.DEFAULT_MATRIX["trader"]
    # Los avisos, sin módulo: le llegan a cualquiera que esté logueado.
    assert get_module_for_path("/api/avisos") is None


def test_los_avisos_filtran_por_el_email_del_que_pregunta():
    """No es un permiso que alguien pueda olvidarse de chequear: **no hay
    parámetro** para pedir los de otro, y el cierre lleva el email en el WHERE
    del UPDATE. Un id ajeno responde "no existe" — que además no confirma que
    ese aviso exista."""
    from pathlib import Path

    from api.routers import avisos
    from api.services import av_agent_vista

    # El invitado queda afuera por una DEPENDENCY del router, no por un `if`
    # adentro del handler: así lo ve el test de superficie y lo ve el propio
    # agente. Un permiso que existe pero no se puede auditar es, para cualquier
    # herramienta, un permiso que no existe.
    gates = {getattr(d.dependency, "__name__", "") for d in avisos.router.dependencies}
    assert "require_no_invitado" in gates, "REGLA #8: el invitado queda afuera"

    # Ninguna ruta acepta una identidad por parámetro: la única que hay sale de
    # `get_user_email`. Se mira la FIRMA y no el texto del archivo — la palabra
    # "destinatario" aparece explicándolo en la documentación, y un test que
    # falla por su propio comentario no prueba nada.
    import inspect
    for ruta in avisos.router.routes:
        params = inspect.signature(ruta.endpoint).parameters
        assert set(params) <= {"request", "email", "body"}, (
            f"{ruta.path} acepta {set(params)}: nada que permita pedir los de otro")
        assert params["email"].default is not inspect.Parameter.empty

    upd = Path(av_agent_vista.__file__).read_text(encoding="utf-8")
    i = upd.index("def resolver_aviso_propio(")
    cuerpo = upd[i:i + 1800]
    assert "lower(para) = %s" in cuerpo, (
        "el dueño va en el WHERE del UPDATE, no en un `if` previo")
