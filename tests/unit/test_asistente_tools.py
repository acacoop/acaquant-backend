"""Tests de api/services/asistente_tools.py — tools read-only del asistente.

SQL mockeado (no tocan la DB). Lo que congelan:
- token-in: la ficha se resuelve a id real SOLO dentro del perímetro.
- token-out: el resultado que vuelve al LLM jamás contiene el id ni el
  nombre real de la cuenta — solo la ficha.
- degradación: cuenta irresoluble / sin datos / tool que explota → mensaje
  claro, nunca excepción ni datos filtrados.
"""
from __future__ import annotations

import pytest

from api.services import asistente_tools as at
from core import pii_gateway as pg

CATALOGO_FAKE = {
    "ids": {"805"},
    "nombres": {"juan perez": "805"},
    "tokens": {"perez": "805"},
    "documentos": set(),
    "operadores": {"martin operetti": "Martin Operetti"},
    "operadores_tokens": {"operetti": "Martin Operetti"},
}


@pytest.fixture(autouse=True)
def entorno(monkeypatch):
    monkeypatch.setattr(pg, "_catalogo", lambda: CATALOGO_FAKE)
    monkeypatch.setattr(at, "_fecha_snapshot", lambda: "2026-07-21")
    monkeypatch.setattr(at, "_aum_totales", lambda fecha: (1_500_000_000.0, 42))
    monkeypatch.setattr(at, "_aum_por_segmento",
                        lambda fecha, top=5: [("PRODUCTORES", 900_000_000.0),
                                              ("SIN SEGMENTO", 600_000_000.0)])
    monkeypatch.setattr(at, "_aum_cuenta", lambda fecha, idc: 12_000_000.0)
    monkeypatch.setattr(at, "_pnl_cuenta", lambda idc: {
        "totales": {"pnl_no_realizado": 500_000.0, "pnl_pasivo": 100_000.0,
                    "pnl_realizado_dia": 0.0, "pnl_total": 600_000.0},
        "computed_at": "2026-07-21 11:00:00+00",
    })


def _mapping_con_cliente():
    _limpio, mapping = pg.tokenize("cómo viene Juan Perez")
    return mapping


def test_resumen_mesa_trae_agregados_sin_nombres():
    r = at.resumen_mesa()
    assert "1.50 mil millones" in r and "42" in r
    assert "PRODUCTORES" in r and "60.0%" in r
    assert "805" not in r and "perez" not in r.lower()


def test_resumen_mesa_sin_snapshot(monkeypatch):
    monkeypatch.setattr(at, "_fecha_snapshot", lambda: None)
    assert "sin datos" in at.resumen_mesa()


def test_rendimiento_cuenta_resuelve_ficha_en_perimetro(monkeypatch):
    mapping = _mapping_con_cliente()
    ficha = next(f for f in mapping["fichas"] if f.startswith("CLIENTE_"))
    resuelto = {}
    original = at._aum_cuenta

    def espia(fecha, idc):
        resuelto["id"] = idc
        return original(fecha, idc)

    monkeypatch.setattr(at, "_aum_cuenta", espia)
    r = at.rendimiento_cuenta(ficha, mapping=mapping)
    assert resuelto["id"] == "805"          # la resolución pasó por el perímetro
    assert ficha in r                        # la respuesta habla en fichas
    assert "805" not in r and "Perez" not in r  # el id/nombre JAMÁS vuelve al LLM
    assert "12.0 millones" in r and "0.60 millones" not in r  # pnl_total formateado aparte


def test_rendimiento_cuenta_ficha_irresoluble():
    r = at.rendimiento_cuenta("CLIENTE_99", mapping={"fichas": {}})
    assert "no pude identificar" in r


def test_ejecutar_dispatcher_y_token_out():
    mapping = _mapping_con_cliente()
    ficha = next(f for f in mapping["fichas"] if f.startswith("CLIENTE_"))
    r = at.ejecutar("rendimiento_cuenta", {"ficha_cuenta": ficha}, mapping=mapping)
    assert ficha in r and "805" not in r and "Perez" not in r


def test_ejecutar_tool_desconocida():
    assert "desconocida" in at.ejecutar("drop_tables", {}, mapping={"fichas": {}})


# ── quien_es: cliente vs operador NO se adivina (caso real 2026-07-21) ──────

def _mock_personas(monkeypatch, cuentas, operadores):
    import api.services.copiloto.navegacion as nv
    monkeypatch.setattr(nv, "_cuentas", lambda: cuentas)
    monkeypatch.setattr(nv, "_operadores", lambda: operadores)


def test_quien_es_distingue_cliente_de_operador(monkeypatch):
    _mock_personas(monkeypatch, [("805", "MOLLO, NICOLAS EZEQUIEL")],
                   [("jc@aca.com", "Javier Curzel")])
    mapping = {"fichas": {"CLIENTE_1": "nicolas mollo", "CLIENTE_2": "javier curzel"}}
    r1 = at.ejecutar("quien_es", {"ficha": "CLIENTE_1"}, mapping=mapping)
    assert "CUENTA de cliente" in r1 and "MOLLO" not in r1   # habla en fichas
    r2 = at.ejecutar("quien_es", {"ficha": "CLIENTE_2"}, mapping=mapping)
    assert "OPERADOR" in r2 and "Curzel" not in r2


def test_quien_es_ambiguo_manda_a_preguntar(monkeypatch):
    _mock_personas(monkeypatch, [("805", "MOLLO, NICOLAS EZEQUIEL")],
                   [("mm@aca.com", "MOLLO, NICOLAS EZEQUIEL")])
    r = at.ejecutar("quien_es", {"ficha": "CLIENTE_1"},
                    mapping={"fichas": {"CLIENTE_1": "mollo"}})
    assert "AMBIGUO" in r and "preguntale al usuario" in r.lower()


def test_quien_es_desconocido(monkeypatch):
    _mock_personas(monkeypatch, [], [])
    r = at.ejecutar("quien_es", {"ficha": "CLIENTE_9"},
                    mapping={"fichas": {"CLIENTE_9": "nadie"}})
    assert "no encontré" in r


def test_ejecutar_tool_que_explota_no_filtra(monkeypatch):
    def _boom():
        raise RuntimeError("secreto interno: cuenta 805 de Juan Perez")
    monkeypatch.setattr(at, "resumen_mesa", _boom)
    r = at.ejecutar("resumen_mesa", {}, mapping={"fichas": {}})
    assert "805" not in r and "Perez" not in r
    assert "falló" in r


def test_token_out_tacha_identidad_que_colara(monkeypatch):
    """Si una tool devolviera un nombre real por accidente, la aduana lo
    tacha ANTES de volver al LLM (cinturón y tirantes)."""
    monkeypatch.setattr(at, "resumen_mesa", lambda: "el mayor tenedor es Juan Perez")
    r = at.ejecutar("resumen_mesa", {}, mapping=pg._mapping_nuevo())
    assert "Perez" not in r and "CLIENTE_" in r


def test_schemas_declarados():
    nombres = {t["function"]["name"] for t in at.TOOLS}
    assert nombres == {"resumen_mesa", "rendimiento_cuenta", "quien_es",
                       "volumen_operado", "aranceles_consolidado"}
    rc = next(t for t in at.TOOLS if t["function"]["name"] == "rendimiento_cuenta")
    assert "ficha_cuenta" in rc["function"]["parameters"]["properties"]
    vo = next(t for t in at.TOOLS if t["function"]["name"] == "volumen_operado")
    assert vo["function"]["parameters"]["required"] == ["desde", "hasta", "por"]


# ── consolidados (volumen / aranceles por dimensión) ─────────────────────────

def _mock_consolidado(monkeypatch, esperado: dict):
    capturado = {}

    def fake(**kw):
        capturado.update(kw)
        return esperado

    import api.services.operaciones_sql as ops
    monkeypatch.setattr(ops, "ops_consolidado", fake)
    return capturado


def test_volumen_operado_formatea_y_pasa_params(monkeypatch):
    capturado = _mock_consolidado(monkeypatch, {
        "metrica": "bruto", "por": "mercado", "desde": "2026-01-01",
        "hasta": "2026-06-30", "moneda": "ARS",
        "filas": [{"clave": "BYMA", "valor": 900_000_000.0, "n": 1200},
                  {"clave": "MAV", "valor": 100_000_000.0, "n": 300}],
        "total": 1_000_000_000.0,
    })
    r = at.ejecutar("volumen_operado",
                    {"desde": "2026-01-01", "hasta": "2026-06-30", "por": "mercado",
                     "excluir_segmento": "AGRO"}, mapping={"fichas": {}})
    assert capturado["metrica"] == "bruto"
    assert capturado["excluir_segmento"] == "AGRO"
    assert "BYMA" in r and "90.0%" in r and "1200 boletos" in r
    assert "TOTAL" in r


def test_aranceles_consolidado_usa_metrica_arancel(monkeypatch):
    capturado = _mock_consolidado(monkeypatch, {
        "metrica": "arancel", "por": "mercado", "desde": "2026-01-01",
        "hasta": "2026-06-30", "moneda": "ARS",
        "filas": [{"clave": "BYMA", "valor": 5_000_000.0, "n": 800}],
        "total": 5_000_000.0,
    })
    r = at.ejecutar("aranceles_consolidado",
                    {"desde": "2026-01-01", "hasta": "2026-06-30", "por": "mercado"},
                    mapping={"fichas": {}})
    assert capturado["metrica"] == "arancel"
    assert "aranceles por mercado" in r and "5.0 millones" in r


def test_consolidado_dimension_invalida_es_jaula():
    from api.services.operaciones_sql import ops_consolidado
    r = ops_consolidado(metrica="bruto", desde="2026-01-01", hasta="2026-06-30",
                        por="; DROP TABLE operaciones")
    assert "invalida" in r["error"]


def test_consolidado_por_operador_ficha_los_nombres(monkeypatch):
    """Decisión b (2026-07-21): los EMPLEADOS tampoco salen — la dimensión
    operador vuelve al LLM con fichas OPERADOR_n, jamás nombres."""
    _mock_consolidado(monkeypatch, {
        "metrica": "arancel", "por": "operador", "desde": "2026-01-01",
        "hasta": "2026-06-30", "moneda": "ARS",
        "filas": [{"clave": "Martin Operetti", "valor": 3_000_000.0, "n": 40},
                  {"clave": "(sin operador)", "valor": 500_000.0, "n": 9}],
        "total": 3_500_000.0,
    })
    mapping = pg._mapping_nuevo()
    r = at.ejecutar("aranceles_consolidado",
                    {"desde": "2026-01-01", "hasta": "2026-06-30", "por": "operador"},
                    mapping=mapping)
    assert "Operetti" not in r and "OPERADOR_1" in r
    assert "(sin operador)" in r          # la huérfana no es una identidad
    assert mapping["fichas"]["OPERADOR_1"] == "Martin Operetti"  # detokeniza al user


def test_consolidado_filtro_por_ficha_operador(monkeypatch):
    capturado = _mock_consolidado(monkeypatch, {
        "metrica": "bruto", "por": "mercado", "desde": "2026-07-01",
        "hasta": "2026-07-21", "moneda": "ARS",
        "filas": [{"clave": "BYMA", "valor": 1_000_000.0, "n": 5}],
        "total": 1_000_000.0,
    })
    mapping = pg._mapping_nuevo()
    ficha = pg.asignar_ficha(mapping, "OPERADOR", "Martin Operetti")
    at.ejecutar("volumen_operado",
                {"desde": "2026-07-01", "hasta": "2026-07-21", "por": "mercado",
                 "ficha_operador": ficha}, mapping=mapping)
    assert capturado["operador_sel"] == "Martin Operetti"  # resuelto en perímetro


def test_consolidado_sin_filas(monkeypatch):
    _mock_consolidado(monkeypatch, {
        "metrica": "bruto", "por": "segmento", "desde": "2026-01-01",
        "hasta": "2026-01-02", "moneda": "ARS", "filas": [], "total": 0.0,
    })
    r = at.ejecutar("volumen_operado",
                    {"desde": "2026-01-01", "hasta": "2026-01-02", "por": "segmento"},
                    mapping={"fichas": {}})
    assert "sin operaciones" in r
