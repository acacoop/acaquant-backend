"""Tests del bloque COMERCIAL del asistente (api/services/asistente_comercial.py).

Los mocks usan el shape REAL de cada service — verificado leyendo el service,
no inventado. Es la lección de 2026-07-22: un mock con una clave inventada
convierte al test en cómplice del bug (tres tools quedaron mudas así).

Lo que congelan:
- el GATE de Control Comercial: sin el permiso no se consulta ni la DB;
- que los COMERCIALES vuelvan como OPERADOR_n y los clientes como CLIENTE_n;
- que un EMAIL de comercial NUNCA salga, ni fichado;
- que agregar una lente no rompa el enum ni el despacho.
"""
from __future__ import annotations

import pytest

from api.services import asistente_comercial as ac
from api.services import asistente_tools as at
from core import pii_gateway as pg


@pytest.fixture(autouse=True)
def con_permiso(monkeypatch):
    monkeypatch.setattr(at, "puede_control_comercial", lambda u: True)


def _mapping():
    return pg._mapping_nuevo()


# ── gate ─────────────────────────────────────────────────────────────────────

def test_sin_permiso_no_toca_la_db(monkeypatch):
    """El gate es POR USUARIO (no el rol). Sin el flag, ni una query."""
    import api.services.control_comercial_sql as cc
    llamadas = []
    monkeypatch.setattr(cc, "datos_por_operador",
                        lambda **kw: llamadas.append(1) or {"filas": []})
    monkeypatch.setattr(at, "puede_control_comercial", lambda u: False)
    r = ac.tablero_comercial({"que": "operadores"}, mapping=_mapping(),
                             usuario="sin@flag.com")
    assert "permiso" in r and not llamadas


def test_lente_desconocida_lista_las_validas():
    r = ac.tablero_comercial({"que": "; DROP TABLE"}, mapping=_mapping(), usuario="x@y")
    assert "desconocida" in r and "operadores" in r


# ── lente: ranking de comerciales ───────────────────────────────────────────

def test_operadores_ficha_los_nombres_y_nunca_el_email(monkeypatch):
    import api.services.control_comercial_sql as cc
    monkeypatch.setattr(cc, "datos_por_operador", lambda **kw: {
        "moneda": kw["moneda"], "desde": kw["desde"], "hasta": kw["hasta"], "filas": [
            {"operador_email": "mo@aca.com", "operador_nombre": "Martin Operetti",
             "clientes_activos": 12, "clientes_activos_pct": 20.0,
             "clientes_inactivos": 3, "aum": 5_000_000.0, "aum_pct": -1.5,
             "volumen": 900_000_000.0, "volumen_pct": 12.5,
             "comisiones": 4_000_000.0, "comisiones_pct": None},
        ]})
    mapping = _mapping()
    r = ac.tablero_comercial({"que": "operadores"}, mapping=mapping, usuario="x@y")
    assert "OPERADOR_1" in r and "Operetti" not in r
    assert "mo@aca.com" not in r                    # el email NO sale nunca
    assert "+12.5%" in r and "12 clientes activos" in r
    assert mapping["fichas"]["OPERADOR_1"] == "Martin Operetti"


def test_operador_sin_nombre_no_cae_al_email(monkeypatch):
    """Un comercial sin nombre cargado se reporta como tal: el email es una
    identidad directa y no aporta nada al análisis."""
    import api.services.control_comercial_sql as cc
    monkeypatch.setattr(cc, "datos_por_operador", lambda **kw: {"filas": [
        {"operador_email": "jc@aca.com", "operador_nombre": "jc@aca.com",
         "volumen": 1.0, "comisiones": 1.0, "aum": 1.0,
         "clientes_activos": 1, "clientes_inactivos": 0}]})
    r = ac.tablero_comercial({"que": "operadores"}, mapping=_mapping(), usuario="x@y")
    assert "jc@aca.com" not in r and "sin nombre cargado" in r


# ── lente: objetivos ────────────────────────────────────────────────────────

def test_objetivos_ordena_por_lo_mas_lejos_del_objetivo(monkeypatch):
    import api.services.control_comercial_sql as cc
    monkeypatch.setattr(cc, "objetivos_vs_actual", lambda **kw: {"filas": [
        {"operador_email": "a@x", "operador_nombre": "Ana Alta",
         "volumen_actual": 90.0, "volumen_objetivo": 100.0,
         "comisiones_actual": 9.0, "comisiones_objetivo": 10.0, "pct_alcanzado": 90.0},
        {"operador_email": "b@x", "operador_nombre": "Beto Bajo",
         "volumen_actual": 20.0, "volumen_objetivo": 100.0,
         "comisiones_actual": 2.0, "comisiones_objetivo": 10.0, "pct_alcanzado": 20.0},
    ]})
    r = ac.tablero_comercial({"que": "objetivos"}, mapping=_mapping(), usuario="x@y")
    filas = [ln for ln in r.splitlines() if ln.startswith("  - ")]
    assert "20%" in filas[0] and "90%" in filas[1]   # primero el que va peor
    assert "Beto" not in r and "Ana" not in r


@pytest.mark.parametrize("desde,hasta,espera", [
    ("2026-06-01", "2026-06-30", None),                 # mes entero → sin aviso
    ("2026-05-01", "2026-06-30", None),                 # dos meses enteros
    ("2026-07-01", "2026-07-22", "todavía no terminó"),  # mes en curso
    ("2026-06-22", "2026-07-22", "NO cubre meses enteros"),  # recorte a caballo
])
def test_objetivos_avisa_cuando_el_periodo_no_es_de_meses_enteros(
        monkeypatch, desde, hasta, espera):
    """Los objetivos se cargan POR MES. Contra un período recortado el %
    alcanzado sale bajo y parece un problema de gestión cuando es un problema
    de recorte — un porcentaje plausible y mal calibrado es peor que ninguno."""
    import api.services.control_comercial_sql as cc
    monkeypatch.setattr(cc, "objetivos_vs_actual", lambda **kw: {"filas": [
        {"operador_nombre": "Ana", "volumen_actual": 50.0, "volumen_objetivo": 100.0,
         "comisiones_actual": 5.0, "comisiones_objetivo": 10.0, "pct_alcanzado": 50.0}]})
    r = ac.tablero_comercial({"que": "objetivos", "desde": desde, "hasta": hasta},
                             mapping=_mapping(), usuario="x@y")
    if espera is None:
        assert "⚠" not in r
    else:
        assert espera in r


def test_objetivos_sin_objetivos_cargados_lo_dice(monkeypatch):
    import api.services.control_comercial_sql as cc
    monkeypatch.setattr(cc, "objetivos_vs_actual", lambda **kw: {"filas": [
        {"operador_nombre": "X", "volumen_actual": 5.0, "volumen_objetivo": None,
         "comisiones_actual": 1.0, "comisiones_objetivo": None, "pct_alcanzado": None}]})
    r = ac.tablero_comercial({"que": "objetivos"}, mapping=_mapping(), usuario="x@y")
    assert "no hay objetivos cargados" in r


# ── lente: estado de la cartera ─────────────────────────────────────────────

def test_cartera_agrupa_por_estado_y_marca_los_dormidos_grandes(monkeypatch):
    import api.services.comercial_sql as cs
    monkeypatch.setattr(cs, "analisis_comercial", lambda **kw: {
        "operador": kw["operador"], "dias_activa": 45, "dias_dormida": 90,
        "fecha": None, "clientes": [
            {"id_cuenta": "805", "denominacion": "PEREZ, JUAN", "aum": 700.0,
             "ultima_op": "2026-07-20", "dias_sin_operar": 2, "estado": "ACTIVA"},
            {"id_cuenta": "9", "denominacion": "GOMEZ, ANA", "aum": 300.0,
             "ultima_op": "2026-01-05", "dias_sin_operar": None, "estado": "DORMIDA"},
        ]})
    mapping = _mapping()
    r = ac.tablero_comercial({"que": "cartera"}, mapping=mapping, usuario="x@y")
    assert "ACTIVA: 1 clientes" in r and "DORMIDA: 1 clientes" in r
    assert "70.0% del AuM" in r
    assert "a quién llamar primero" in r
    assert "GOMEZ" not in r and "PEREZ" not in r     # los clientes van fichados
    assert "CLIENTE_1" in r


def test_cartera_de_un_operador_resuelve_la_ficha(monkeypatch):
    import api.services.comercial_sql as cs
    visto = {}
    monkeypatch.setattr(cs, "analisis_comercial",
                        lambda **kw: visto.update(kw) or {"clientes": []})
    mapping = _mapping()
    ficha = pg.asignar_ficha(mapping, "OPERADOR", "Martin Operetti")
    monkeypatch.setattr(pg, "operador_de_ficha", lambda f, m: "Martin Operetti")
    ac.tablero_comercial({"que": "cartera", "ficha_operador": ficha},
                         mapping=mapping, usuario="x@y")
    assert visto["operador"] == "Martin Operetti"    # resuelto DENTRO del perímetro


def test_cartera_ficha_irresoluble_manda_a_preguntar(monkeypatch):
    monkeypatch.setattr(pg, "operador_de_ficha", lambda f, m: None)
    r = ac.tablero_comercial({"que": "cartera", "ficha_operador": "OPERADOR_9"},
                             mapping=_mapping(), usuario="x@y")
    assert "no pude resolver" in r and "preguntale al usuario" in r


# ── lente: cuentas sin comercial ────────────────────────────────────────────

def test_sin_operador_ficha_y_separa_los_no_clientes(monkeypatch):
    import api.services.sin_operador as so
    monkeypatch.setattr(so, "cuentas_sin_operador", lambda: {
        "clientes_sin_operador": [
            {"id_cuenta": "805", "vol": 1_000_000.0, "cuenta": "[805] PEREZ, JUAN",
             "denominacion": "PEREZ, JUAN", "estado": "Activa"}],
        "no_clientes": [], "resumen_no_clientes": {},
        "n_clientes_sin_op": 1, "n_no_clientes": 4, "sin_clasificar": 0})
    r = ac.tablero_comercial({"que": "sin_operador"}, mapping=_mapping(), usuario="x@y")
    assert "1 son clientes reales" in r and "otras 4 son internas" in r
    assert "PEREZ" not in r and "CLIENTE_1" in r


def test_sin_operador_todo_asignado(monkeypatch):
    import api.services.sin_operador as so
    monkeypatch.setattr(so, "cuentas_sin_operador", lambda: {
        "clientes_sin_operador": [], "no_clientes": [], "n_no_clientes": 0})
    r = ac.tablero_comercial({"que": "sin_operador"}, mapping=_mapping(), usuario="x@y")
    assert "no hay cuentas" in r


# ── contrato ────────────────────────────────────────────────────────────────

def test_el_enum_y_la_description_derivan_del_registro():
    """Agregar una lente NO puede exigir tocar el schema a mano: si el enum se
    escribiera aparte, el modelo pediría lentes que el código no ejecuta."""
    schema = ac.TOOLS_COMERCIAL[0]["function"]
    assert schema["parameters"]["properties"]["que"]["enum"] == sorted(ac._LENTES)
    for lente in ac._LENTES:
        assert lente in schema["description"]


def test_la_tool_esta_enganchada_en_el_aggregator():
    assert "tablero_comercial" in at.herramientas_declaradas()
    assert "tablero_comercial" in at._HANDLERS


def test_el_gate_recibe_el_usuario_real_desde_el_dispatcher(monkeypatch):
    """El gate vive adentro del dominio → si el aggregator no propaga el
    usuario, el gate no puede decidir y el chat se vuelve puerta trasera.
    Se verifica en el CIRCUITO completo (ejecutar → dominio → gate), no
    mockeando el eslabón del medio."""
    vistos = []
    monkeypatch.setattr(at, "puede_control_comercial",
                        lambda u: vistos.append(u) or False)
    r = at.ejecutar("tablero_comercial", {"que": "operadores"},
                    mapping={"fichas": {}}, usuario="jefe@x.com")
    assert vistos == ["jefe@x.com"]
    assert "permiso" in r
