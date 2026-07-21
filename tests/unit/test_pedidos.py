"""Tests del BUZÓN DE PEDIDOS (copiloto/pedidos.py + la tool común).

Congelan: que la tool esté disponible en TODAS las vistas (el pedido se hace
donde surgió, no solo en la guía), que el texto se destokenice antes de
guardarse (queremos el real, la tabla es nuestra) y que un fallo del buzón
JAMÁS rompa la respuesta del copiloto.
"""
from __future__ import annotations

from api.services.copiloto import pedidos


def test_tool_declarada():
    f = pedidos.TOOL_PEDIDO["function"]
    assert f["name"] == pedidos.NOMBRE_TOOL
    assert set(f["parameters"]["required"]) == {"texto", "titulo", "tipo"}
    assert "mejora" in f["parameters"]["properties"]["tipo"]["enum"]


def test_registra_y_normaliza(monkeypatch):
    guardado = {}
    monkeypatch.setattr(pedidos, "registrar",
                        lambda **kw: guardado.update(kw) or 7)
    salida = pedidos.ejecutar_pedido(
        {"texto": "estaría bueno filtrar por cartera", "titulo": "Filtro por cartera",
         "tipo": "mejora"},
        usuario="jefe@x.com", vista="operaciones", contexto="la pregunta previa")
    assert "#7" in salida and "anotado" in salida.lower()
    assert guardado["vista"] == "operaciones" and guardado["tipo"] == "mejora"
    assert guardado["contexto"] == "la pregunta previa"


def test_tipo_invalido_cae_a_otro(monkeypatch):
    capturado = {}
    monkeypatch.setattr(pedidos, "_MAX_TEXTO", 100)

    class _Cur:
        def execute(self, sql, params): capturado["params"] = params
        def fetchone(self): return (1,)
        def __enter__(self): return self
        def __exit__(self, *a): ...

    class _Conn(_Cur):
        def cursor(self): return _Cur()

    monkeypatch.setattr("core.postgres.get_pool", lambda: type("P", (), {
        "connection": lambda self: _Conn()})())
    pedidos.registrar(texto="x", titulo="t", tipo="inventado",
                      usuario="u", vista="home")
    assert capturado["params"][2] == "otro"


def test_texto_vacio_no_registra():
    assert pedidos.registrar(texto="   ", titulo="t", tipo="mejora",
                             usuario="u", vista="home") is None


def test_destokeniza_antes_de_guardar(monkeypatch):
    """La vista puede tener aduana: el modelo nos pasa el texto tokenizado y
    el buzón (que es NUESTRO) tiene que guardar el real."""
    guardado = {}
    monkeypatch.setattr(pedidos, "registrar", lambda **kw: guardado.update(kw) or 1)
    mapping = {"fichas": {"CLIENTE_1": "Juan Perez"}}
    pedidos.ejecutar_pedido(
        {"texto": "no encuentro las operaciones de CLIENTE_1", "titulo": "Ops de CLIENTE_1",
         "tipo": "falta_dato"},
        usuario="u@x.com", vista="ayuda", mapping=mapping)
    assert "Juan Perez" in guardado["texto"] and "CLIENTE_1" not in guardado["texto"]
    assert "Juan Perez" in guardado["titulo"]


def test_fallo_del_buzon_no_miente(monkeypatch):
    monkeypatch.setattr(pedidos, "registrar", lambda **kw: None)
    salida = pedidos.ejecutar_pedido({"texto": "x", "titulo": "t", "tipo": "bug"},
                                     usuario="u", vista="home")
    assert "no pude registrar" in salida and "sin inventar" in salida


def test_registrar_nunca_levanta(monkeypatch):
    def _boom():
        raise RuntimeError("db caída")
    monkeypatch.setattr("core.postgres.get_pool", _boom)
    assert pedidos.registrar(texto="x", titulo="t", tipo="mejora",
                             usuario="u", vista="home") is None


def test_la_tool_esta_en_TODAS_las_vistas(monkeypatch):
    """El pedido se hace DONDE surgió: el motor suma la tool común a las de
    la vista, tenga o no tools propias."""
    from api.services import copiloto

    vistos: dict = {}

    def fake_tools(tarea, system=None, user=None, tools=None, ejecutar=None,
                   usuario=None, detalle=None, historial=None):
        vistos["tools"] = [t["function"]["name"] for t in tools]
        vistos["ejecutar"] = ejecutar
        return "ok", 1, ""

    monkeypatch.setattr("core.ai.completar_con_tools", fake_tools)
    monkeypatch.setattr("core.ai.motivo_presupuesto", lambda u: None)
    monkeypatch.setattr("core.roles.has_access", lambda u, m: True)
    monkeypatch.setattr("core.roles.get_user_role", lambda u: "admin")
    monkeypatch.setitem(
        copiloto.VISTAS, "fake",
        {"titulo": "Fake", "modulo": "home",
         "fetch": lambda params=None: [{"a": 1}],
         "columnas": [("a", "a")], "reglas": "r"})

    out = copiloto.preguntar("fake", "hola", usuario="u@x.com")
    assert out["ok"] is True
    assert pedidos.NOMBRE_TOOL in vistos["tools"]          # vista SIN tools propias
    # y el dispatcher la resuelve
    monkeypatch.setattr(pedidos, "registrar", lambda **kw: 5)
    assert "#5" in vistos["ejecutar"](pedidos.NOMBRE_TOOL,
                                      {"texto": "t", "titulo": "T", "tipo": "mejora"})
