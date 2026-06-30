"""Tests de la extracción OHLC del snapshot (jobs.cedears_ohlc_daily._fila)."""
from __future__ import annotations

from datetime import date

from jobs.cedears_ohlc_daily import _fila

_FECHA = date(2026, 6, 30)


def test_fila_ok_close_es_el_last():
    data = {"ticker_corto": "RKLB", "open": 100, "high": 110, "low": 95, "last": 108, "close": 99}
    f = _fila(data, _FECHA)
    assert f == {
        "ticker_corto": "RKLB", "fecha": _FECHA,
        "open": 100.0, "high": 110.0, "low": 95.0, "close": 108.0,  # close = last, NO el close de ayer
    }


def test_fila_descarta_papel_que_no_opero():
    # sin precios (no operó) → None, no se guarda basura
    assert _fila({"ticker_corto": "XXXX", "high": 0, "low": 0, "last": 0}, _FECHA) is None
    assert _fila({"ticker_corto": "YYYY", "high": None, "low": None, "last": None}, _FECHA) is None
    assert _fila({"high": 10, "low": 9, "last": 9.5}, _FECHA) is None  # sin ticker


def test_fila_open_invalido_queda_none_pero_guarda_hlc():
    f = _fila({"ticker_corto": "SNDK", "open": 0, "high": 200, "low": 190, "last": 197}, _FECHA)
    assert f is not None
    assert f["open"] is None and f["high"] == 200.0 and f["close"] == 197.0
