"""SALUD es ADMIN-ONLY. Guarda de regresión del incidente 2026-08-10.

⚠️ **REPUNTADO al 2026-08-19.** SALUD salió del front y su panel de Manager se dio
de baja (`AV_AGENT.md` §0.l): los `/api/manager/salud*` que este test recorría ya
no existen, así que los 30 casos devolvían **404 en vez de 403** y el archivo
entero estaba en rojo. Un guard de seguridad que apunta a rutas borradas no
protege nada: bloquea el CI y, peor, deja de mirar la puerta que sí está abierta.

Ahora recorre la puerta real —`/api/ia/av-agent/salud*`, admin-only— y sigue
exigiendo lo mismo: que ningún rol que no sea admin llegue al estado interno del
sistema. Un 404 pasa a ser FALLA y no un aprobado: si alguien renombra la ruta,
este test se entera en vez de festejar el 404.

Se reportó que las alertas de SALUD las veía gente que no es admin. El gate del
código estaba bien, pero no había NADA que lo fijara: cualquier refactor de
`api/routers/manager/__init__.py` (mover un include, cambiar una constante de
gate) podía abrir los endpoints sin que se notara — y lo que se filtra es el
estado interno del sistema: nombres de jobs, tablas, evidencia de fallas y el
diagnóstico con IA.

Este test recorre TODOS los endpoints de salud con TODOS los roles de la matriz
y exige 403 para cualquiera que no tenga el módulo `manager`. No mockea el gate:
levanta la app real y pega por HTTP.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import core.roles as R
from api.main import app

# (método, path). SALUD vive adentro del agente y casi todo es POST — pedirlos
# con GET daría 405 y un 405 no prueba nada sobre el permiso.
_PATHS: list[tuple[str, str]] = [
    ("POST", "/api/ia/av-agent/salud"),
    ("GET", "/api/ia/av-agent/salud/pendientes"),
    ("POST", "/api/ia/av-agent/salud/vistos"),
    ("POST", "/api/ia/av-agent/salud/silenciar"),
    ("POST", "/api/ia/av-agent/salud/recontrolar?control_id=x"),
]

# El agente es ADMIN-ONLY (`require_admin`), no «cualquiera con el módulo
# manager»: acá se ve el estado interno del sistema entero.
_ROLES_SIN_ADMIN = [r for r in R.DEFAULT_MATRIX if r != "admin"]


@pytest.fixture
def client(monkeypatch):
    """App real con la matriz DEFAULT cacheada (sin tocar Postgres)."""
    monkeypatch.setattr(R, "_matrix_cache", (9e18, dict(R.DEFAULT_MATRIX)), raising=False)
    return TestClient(app)


def _con_rol(monkeypatch, rol: str) -> str:
    """Pinea un email a un rol vía el cache del RBAC (evita la DB)."""
    email = f"{rol}@test.local"
    R._role_by_email[email] = (9e18, rol)
    return email


@pytest.mark.parametrize("rol", _ROLES_SIN_ADMIN)
@pytest.mark.parametrize(("metodo", "path"), _PATHS)
def test_rol_sin_admin_no_ve_salud(client, monkeypatch, rol: str,
                                   metodo: str, path: str):
    email = _con_rol(monkeypatch, rol)
    r = client.request(metodo, path, json={},
                       headers={"x-acaquant-user-email": email})
    assert r.status_code == 403, (
        f"FUGA: el rol {rol!r} obtuvo {r.status_code} en {path}. SALUD expone el "
        f"estado interno del sistema y es admin-only."
    )


def test_identidad_de_maquina_tampoco(client):
    """Sin email propagado (service token / cron) NO hay acceso: rol `none`."""
    r = client.post("/api/ia/av-agent/salud", json={})
    assert r.status_code == 403


@pytest.mark.parametrize(("metodo", "path"), _PATHS)
def test_la_ruta_EXISTE_y_pide_admin(metodo: str, path: str):
    """**Un 404 es una falla, no un aprobado** — y esa es la lección del archivo.

    Con los `/api/manager/salud*` borrados, los 30 casos de arriba devolvían 404
    y el guard seguía «pasando» conceptualmente: desde afuera un 404 y un 403 se
    ven igual de cerrados. Si mañana alguien renombra la puerta, esto salta en
    vez de proteger el aire.

    Se mira la TABLA DE RUTAS y no se pega por HTTP a propósito: pegar ejecuta el
    handler, que toca Postgres, y entonces el test diría «la ruta no existe»
    cuando lo único que pasa es que no hay base — exactamente la clase de falso
    veredicto que este proyecto persigue. Se usa `api.superficie`, que es la
    única forma correcta de recorrer la superficie (los `include_router` no
    aparecen en `app.routes`; ver `CLAUDE.md`).
    """
    from api import superficie
    limpio = path.split("?")[0]
    rs = [r for r in superficie.rutas() if r.path == limpio and metodo in r.metodos]
    assert rs, f"{metodo} {limpio} ya no existe: el guard quedó apuntando al aire"
    assert all("require_admin" in r.gates for r in rs), (
        f"{limpio} dejó de pedir `require_admin`")
