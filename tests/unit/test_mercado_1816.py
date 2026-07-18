"""Tests del cliente de 1816 (core/mercado_1816.py) — parte pura, sin red.

Congela el aplanado de series (el shape verificado contra la API real 2026-07-18:
instrumentos.<ticker>.<campo> = [[fecha, valor], …]). Doc: docs/VISTA_RESEARCH.md.
"""
from __future__ import annotations

from core import mercado_1816


def test_parse_series_aplana_y_saltea_nulos():
    data = {
        "fuente": "byma", "moneda": "ars", "plazo": 1, "convencionTna": "180-360",
        "instrumentos": {
            "AL30": {
                "precioClean": [["2026-06-18", 134026.3], ["2026-06-19", 135000.0]],
                "tea": [["2026-06-18", 0.0926], ["2026-06-19", None]],  # null se saltea
            },
        },
    }
    filas = mercado_1816.parse_series(data)
    # 2 de precioClean + 1 de tea (el null no cuenta) = 3
    assert len(filas) == 3
    al30_tea = [f for f in filas if f["campo"] == "tea"]
    assert al30_tea == [{
        "ticker": "AL30", "fecha": "2026-06-18", "campo": "tea", "valor": 0.0926,
        "fuente": "byma", "moneda": "ars", "plazo": 1, "convencion_tna": "180-360",
    }]


def test_parse_series_vacio_no_rompe():
    assert mercado_1816.parse_series({}) == []
    assert mercado_1816.parse_series({"instrumentos": {}}) == []
    assert mercado_1816.parse_series({"instrumentos": {"AL30": {"tea": []}}}) == []


def test_disponible_depende_de_la_key(monkeypatch):
    monkeypatch.delenv("MERCADO_1816_API_KEY", raising=False)
    assert mercado_1816.disponible() is False
    monkeypatch.setenv("MERCADO_1816_API_KEY", "x")
    assert mercado_1816.disponible() is True
