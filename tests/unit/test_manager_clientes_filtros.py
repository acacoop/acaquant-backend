"""Tests de los filtros de GET /api/manager/clientes (Manager → Clientes).

Nace del PEDIDO #1 del buzón ("agregar el nivel 2 como filtro en la
segmentación"). Lo que congelan:

- que el nivel 2 filtre de verdad y se combine con el nivel 1 (AND);
- que el nombre de la columna NUNCA venga del request — sale de la whitelist
  de campos editables, así el filtro no puede apuntar a una columna arbitraria;
- que sin filtros el WHERE quede vacío (no se cuela un `nivel_2 = ''`).

El SQL se intercepta: no toca la DB.
"""
from __future__ import annotations

import pytest

from api.services import clientes_admin_sql as mod


def _listar(**kw):
    """Invoca el SERVICE directo (la lógica vive en clientes_admin_sql; el
    router es plumbing). Los defaults son None reales — ya no existe la trampa
    de los objetos Query() que rompió estos tests cuando el endpoint sumó
    params sin actualizar el harness."""
    return mod.list_clientes(**kw)


@pytest.fixture
def capturar(monkeypatch):
    """Reemplaza el ejecutor de SQL y devuelve lo que se le pasó."""
    visto = {}

    def _fake(sql, params=None):
        visto["sql"] = sql
        visto["params"] = params or {}
        return []

    monkeypatch.setattr(mod, "_q", _fake)
    return visto


def test_sin_filtros_no_hay_where(capturar):
    _listar()
    assert "WHERE" not in capturar["sql"]
    assert capturar["params"] == {}


def test_nivel_2_filtra(capturar):
    _listar(nivel_2="SOJA")
    assert "c.nivel_2 = %(nivel_2)s" in capturar["sql"]
    assert capturar["params"]["nivel_2"] == "SOJA"


def test_nivel_1_y_nivel_2_se_combinan_con_and(capturar):
    """La razón de ser del pedido: acotar DENTRO de un nivel 1, no reemplazarlo."""
    _listar(nivel_1="AGRO", nivel_2="SOJA")
    assert "c.nivel_1 = %(nivel_1)s" in capturar["sql"]
    assert "c.nivel_2 = %(nivel_2)s" in capturar["sql"]
    assert " AND " in capturar["sql"]
    assert capturar["params"] == {"nivel_1": "AGRO", "nivel_2": "SOJA"}


def test_nivel_vacio_no_agrega_condicion(capturar):
    """'— todos —' manda cadena vacía: no puede convertirse en `= ''`, que no
    matchea nada y dejaría la tabla en blanco sin que se vea por qué."""
    _listar(nivel_1="", nivel_2="")
    assert "WHERE" not in capturar["sql"]


def test_la_columna_sale_de_la_whitelist_no_del_request():
    """El valor va parametrizado, pero el NOMBRE de columna se interpola en el
    SQL. Por eso sale de _EDITABLE_FIELDS y no de nada que mande el cliente."""
    assert "nivel_1" in mod._EDITABLE_FIELDS
    assert "nivel_2" in mod._EDITABLE_FIELDS


def test_campo_vacio_solo_acepta_campos_conocidos(capturar):
    """Mismo criterio para el otro filtro que interpola un nombre de columna."""
    _listar(campo_vacio="'; DROP TABLE comitentes; --")
    assert "DROP" not in capturar["sql"]
    assert "WHERE" not in capturar["sql"]
