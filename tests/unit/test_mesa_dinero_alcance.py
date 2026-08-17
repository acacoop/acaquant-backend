"""ACCESO a Mesa de Dinero — alcance COMPLETO vs SOLO RESULTADOS (2026-08-17).

Congela las dos reglas que hacen que el acceso parcial sea un permiso y no un
adorno de la pantalla:

1. Gana el acceso MÁS AMPLIO. Estar en la lista de solo-resultados no puede
   recortarle la vista a un admin, a un lector completo ni a un escritor.
2. Las tabs que NO son RESULTADOS se cortan en el BACKEND. `/ops`, `/resumen` y
   `/retorno` llevan `require_vista_completa`: si mañana alguien agrega un
   endpoint con el detalle de las operaciones y se olvida del gate, esconder la
   solapa en el front no alcanza — el dato queda a un request de distancia.

`alcance` está cacheado por email → cada caso usa un email distinto en vez de
invalidar (misma razón por la que el cache existe: la llave incluye el email).
"""
from __future__ import annotations

import pytest

from api.services import mesa_dinero as svc


def _fake_q(filas):
    """Reemplaza `_q`: la query de `alcance` devuelve una fila por lista donde
    el email está, con `full`=1 (lectores/escritores) o 0 (solo resultados)."""
    return lambda *a, **k: list(filas)


@pytest.fixture(autouse=True)
def _rol_no_admin(monkeypatch):
    monkeypatch.setattr("core.roles.get_user_role", lambda e: "sales")


def test_sin_ninguna_lista_no_entra(monkeypatch):
    monkeypatch.setattr(svc, "_q", _fake_q([]))
    assert svc.alcance(email="nadie@aca.com") is None
    assert svc.puede_ver(email="nadie@aca.com") is False
    assert svc.ve_todo("nadie@aca.com") is False


def test_lector_completo_ve_todo(monkeypatch):
    monkeypatch.setattr(svc, "_q", _fake_q([{"full": 1}]))
    assert svc.alcance(email="lector@aca.com") == svc.ALCANCE_TODO
    assert svc.ve_todo("lector@aca.com") is True


def test_solo_resultados_entra_pero_no_ve_todo(monkeypatch):
    monkeypatch.setattr(svc, "_q", _fake_q([{"full": 0}]))
    assert svc.alcance(email="comercial@aca.com") == svc.ALCANCE_RESULTADOS
    assert svc.puede_ver(email="comercial@aca.com") is True   # entra a la vista
    assert svc.ve_todo("comercial@aca.com") is False          # …pero solo a RESULTADOS


def test_gana_el_acceso_mas_amplio(monkeypatch):
    """En las DOS listas (completa + solo resultados) → ve todo. Una lista que
    recorta según el orden en que se consulte sería una fuente de sorpresas."""
    monkeypatch.setattr(svc, "_q", _fake_q([{"full": 1}, {"full": 0}]))
    assert svc.alcance(email="jefe.mesa@aca.com") == svc.ALCANCE_TODO


def test_admin_ve_todo_sin_tocar_la_base(monkeypatch):
    monkeypatch.setattr("core.roles.get_user_role", lambda e: "admin")

    def _explota(*a, **k):
        raise AssertionError("admin no debería consultar las allowlists")

    monkeypatch.setattr(svc, "_q", _explota)
    assert svc.alcance(email="admin@aca.com") == svc.ALCANCE_TODO


def test_opciones_del_acceso_parcial_no_filtra_los_catalogos_de_carga(monkeypatch):
    """Con SOLO RESULTADOS no se devuelven clientes ni observaciones (son del
    formulario de alta, que ese usuario no tiene) y `puede_escribir` es False."""
    monkeypatch.setattr(svc, "alcance", lambda email: svc.ALCANCE_RESULTADOS)
    monkeypatch.setattr(svc, "_traders_validos", lambda: ["Juan"])
    o = svc.opciones(email="comercial@aca.com")
    assert o["alcance"] == svc.ALCANCE_RESULTADOS
    assert o["traders"] == ["Juan"]        # el filtro de la barra sí lo necesita
    assert o["clientes"] == []
    assert o["observaciones"] == []
    assert o["puede_escribir"] is False


def test_las_tabs_que_no_son_resultados_estan_gateadas_en_el_backend():
    """El corte vive en el router, no en el front. `/resultados` y `/opciones`
    quedan afuera a propósito: son justo lo que el acceso parcial SÍ ve."""
    from api.routers import mesa_dinero as router_mod

    gateadas = {
        (r.path, dep.dependency.__name__)
        for r in router_mod.router.routes
        for dep in getattr(r, "dependencies", [])
    }
    protegidas = {p for p, fn in gateadas if fn == "require_vista_completa"}
    assert protegidas == {"/api/mesa-dinero/ops", "/api/mesa-dinero/resumen",
                          "/api/mesa-dinero/retorno"}
