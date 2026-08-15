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


def test_cashflow_arma_path_y_campos(monkeypatch):
    """El ticker va en el PATH (no en query) y `campos` es obligatorio para la API:
    sin default explícito, un llamado sin campos daría 400."""
    visto = {}
    monkeypatch.setattr(mercado_1816, "_get",
                        lambda path, params=None: visto.update(path=path, params=params))
    mercado_1816.cashflow(" al30 ")
    assert visto["path"] == "/v1/mercado/cashflow/AL30"
    assert visto["params"]["campos"] == list(mercado_1816.CAMPOS_CASHFLOW)

    mercado_1816.cashflow("GD30", ["flujoTotal"])
    assert visto["params"] == {"campos": ["flujoTotal"]}


def test_cashflow_rechaza_ticker_corto():
    """La API pide >= 3 caracteres: se corta acá para no gastar el request."""
    for malo in ("", "  ", "AL"):
        try:
            mercado_1816.cashflow(malo)
        except mercado_1816.Error1816:
            continue
        raise AssertionError(f"debió rechazar {malo!r}")


def test_instrumentos_solo_performing(monkeypatch):
    """`solo_performing=False` es lo que agrega los VENCIDOS; omitirlo NO manda el
    parámetro (deja el default de la API)."""
    visto = {}
    monkeypatch.setattr(mercado_1816, "_get",
                        lambda path, params=None: visto.update(params=params))
    mercado_1816.instrumentos(curva_id=8)
    assert visto["params"] == {"curvaId": 8}
    mercado_1816.instrumentos(curva_id=8, solo_performing=False)
    assert visto["params"] == {"soloPerforming": "false", "curvaId": 8}


def test_disponible_depende_de_la_key(monkeypatch):
    monkeypatch.delenv("MERCADO_1816_API_KEY", raising=False)
    assert mercado_1816.disponible() is False
    monkeypatch.setenv("MERCADO_1816_API_KEY", "x")
    assert mercado_1816.disponible() is True
