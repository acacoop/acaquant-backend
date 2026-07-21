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
    assert nombres == {"resumen_mesa", "rendimiento_cuenta"}
    rc = next(t for t in at.TOOLS if t["function"]["name"] == "rendimiento_cuenta")
    assert "ficha_cuenta" in rc["function"]["parameters"]["properties"]
