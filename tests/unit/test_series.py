"""Tests de copiloto/series.py — la SERIE HISTÓRICA genérica.

Es la tool que ataca el hueco #1 de la auditoría ("el sistema no sabe
contestar contra qué"). Lo que congelan:
- el CONTRATO de salida: stats de valor relativo + muestra RALIFICADA, nunca
  los puntos crudos (control de tokens en un solo lugar);
- que el percentil y el z los calcule el CÓDIGO (al modelo le está prohibido);
- que agregar una serie sea una fila del registro, no una tool nueva;
- degradación: serie desconocida, sin datos, reader roto.
"""
from __future__ import annotations

from api.services.copiloto import series as S


def _serie_fake(n: int, base: float = 100.0):
    """n puntos crecientes → el último es el máximo (percentil 100). Fechas
    ISO REALES: la tool ordena lexicográficamente (correcto para YYYY-MM-DD),
    así que un fixture con días de 3 dígitos daría un orden falso."""
    from datetime import date, timedelta

    d0 = date(2026, 1, 1)
    return [((d0 + timedelta(days=i)).isoformat(), base + i) for i in range(n)]


def test_contrato_de_salida(monkeypatch):
    monkeypatch.setitem(S._SERIES, "test", {
        "etiqueta": "serie de prueba", "unidad": "%",
        "reader": lambda c, d, h: _serie_fake(10), "ayuda": "-"})
    out = S.serie_historica("test", "X")
    assert "serie de prueba" in out and "10 registros" in out
    assert "último" in out and "mín" in out and "máx" in out and "media" in out
    assert "VALOR RELATIVO" in out and "percentil" in out
    assert "muestra:" in out


def test_percentil_y_lectura_los_calcula_el_codigo(monkeypatch):
    # último = el máximo → percentil 100 → "alto contra su historia"
    monkeypatch.setitem(S._SERIES, "test", {
        "etiqueta": "t", "unidad": "", "reader": lambda c, d, h: _serie_fake(20),
        "ayuda": "-"})
    assert "percentil 100" in S.serie_historica("test")
    assert "alto contra su historia" in S.serie_historica("test")
    # invertida → el último es el mínimo → percentil bajo
    monkeypatch.setitem(S._SERIES, "test2", {
        "etiqueta": "t", "unidad": "",
        "reader": lambda c, d, h: list(reversed(_serie_fake(20))), "ayuda": "-"})
    out = S.serie_historica("test2")
    # el reader devuelve desordenado a propósito: la tool ORDENA por fecha
    assert "2026-01-01" in out


def test_la_muestra_es_ralificada_no_cruda(monkeypatch):
    """El modelo NUNCA recibe 400 puntos: es el control de tokens central."""
    monkeypatch.setitem(S._SERIES, "test", {
        "etiqueta": "t", "unidad": "", "reader": lambda c, d, h: _serie_fake(400),
        "ayuda": "-"})
    out = S.serie_historica("test")
    assert "400 registros" in out                      # los cuenta todos
    muestra = out.split("muestra:")[1]
    assert muestra.count(",") <= S._MAX_MUESTRA + 1    # pero muestra pocos


def test_siempre_incluye_el_ultimo_punto(monkeypatch):
    monkeypatch.setitem(S._SERIES, "test", {
        "etiqueta": "t", "unidad": "", "reader": lambda c, d, h: _serie_fake(101),
        "ayuda": "-"})
    out = S.serie_historica("test")
    # el último valor (100+100=200) SIEMPRE entra, aunque el paso no caiga ahí
    assert "200" in out.split("muestra:")[1]


def test_serie_desconocida_enseña_las_validas():
    out = S.serie_historica("inventada")
    assert "no conozco" in out
    for k in S.series_disponibles():
        assert k in out


def test_sin_datos_lo_dice(monkeypatch):
    monkeypatch.setitem(S._SERIES, "test", {
        "etiqueta": "t", "unidad": "", "reader": lambda c, d, h: [], "ayuda": "-"})
    assert "sin datos" in S.serie_historica("test", "X")


def test_reader_roto_no_levanta(monkeypatch):
    def _boom(c, d, h):
        raise RuntimeError("db caída")
    monkeypatch.setitem(S._SERIES, "test", {
        "etiqueta": "t", "unidad": "", "reader": _boom, "ayuda": "-"})
    assert "no pude leer" in S.serie_historica("test")


def test_registro_declara_todas_las_familias():
    """Agregar una serie es una FILA del registro, no una tool nueva."""
    for nombre, cfg in S._SERIES.items():
        assert callable(cfg["reader"]), nombre
        assert cfg["ayuda"] and cfg["etiqueta"], nombre
    # y el enum del schema se deriva del registro (no se escribe a mano)
    enum = S.TOOL_SERIE["function"]["parameters"]["properties"]["que"]["enum"]
    assert enum == sorted(S._SERIES)


def test_la_tool_esta_en_TODAS_las_vistas(monkeypatch):
    from api.services import copiloto

    visto = {}

    def fake_tools(tarea, system=None, user=None, tools=None, ejecutar=None,
                   usuario=None, detalle=None, historial=None):
        visto["tools"] = [t["function"]["name"] for t in tools]
        visto["ejecutar"] = ejecutar
        return "ok", 1, ""

    monkeypatch.setattr("core.ai.completar_con_tools", fake_tools)
    monkeypatch.setattr("core.ai.motivo_presupuesto", lambda u: None)
    monkeypatch.setattr("core.roles.has_access", lambda u, m: True)
    monkeypatch.setattr("core.roles.get_user_role", lambda u: "admin")
    monkeypatch.setitem(copiloto.VISTAS, "fake", {
        "titulo": "Fake", "modulo": "home",
        "fetch": lambda params=None: [{"a": 1}],
        "columnas": [("a", "a")], "reglas": "r"})
    out = copiloto.preguntar("fake", "hola", usuario="u@x.com")
    assert out["ok"] and "serie_historica" in visto["tools"]
    # y el dispatcher la resuelve
    monkeypatch.setitem(S._SERIES, "test", {
        "etiqueta": "t", "unidad": "", "reader": lambda c, d, h: _serie_fake(5),
        "ayuda": "-"})
    assert "5 registros" in visto["ejecutar"]("serie_historica", {"que": "test"})


def test_tambien_la_tiene_el_asistente_de_negocio():
    from api.services import asistente_tools as at
    assert "serie_historica" in {t["function"]["name"] for t in at.TOOLS}
