"""SALUD es ADMIN-ONLY. Guarda de regresión del incidente 2026-08-10.

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

_PATHS = [
    "/api/manager/salud",
    "/api/manager/salud?solo_problemas=true",
    "/api/manager/salud/historial",
    "/api/manager/salud/detalle?chequeo_id=x",
    "/api/manager/salud/diagnostico?chequeo_id=x",
]

_ROLES_SIN_MANAGER = [r for r, mods in R.DEFAULT_MATRIX.items() if "manager" not in mods]


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


@pytest.mark.parametrize("rol", _ROLES_SIN_MANAGER)
@pytest.mark.parametrize("path", _PATHS)
def test_rol_sin_manager_no_ve_salud(client, monkeypatch, rol: str, path: str):
    email = _con_rol(monkeypatch, rol)
    r = client.get(path, headers={"x-acaquant-user-email": email})
    assert r.status_code == 403, (
        f"FUGA: el rol {rol!r} obtuvo {r.status_code} en {path}. SALUD expone el "
        f"estado interno del sistema y es admin-only."
    )


def test_identidad_de_maquina_tampoco(client):
    """Sin email propagado (service token / cron) NO hay acceso: rol `none`."""
    r = client.get("/api/manager/salud")
    assert r.status_code == 403


@pytest.mark.parametrize("path", _PATHS)
def test_admin_si_entra(client, monkeypatch, path: str):
    """La contracara: el gate no puede estar cerrado para el admin."""
    email = _con_rol(monkeypatch, "admin")
    r = client.get(path, headers={"x-acaquant-user-email": email})
    # 404 es una respuesta legítima (chequeo_id inexistente): lo que importa es
    # que NO sea 403 — el gate lo dejó pasar.
    assert r.status_code != 403
