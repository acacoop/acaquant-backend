"""Tests unit de quant/pivot_points.py — sin DB (se mockea la query).

Congela el fix del 2026-08-10: `obtener_4_timeframes` filtra las velas en
Python contra rangos AWARE (UTC) mientras que `_sql_docs_en_rango` devuelve
`fecha` NAIVE. Compararlos como datetime tiraba
`TypeError: can't compare offset-naive and offset-aware datetimes` y el
endpoint /api/scanner/pivot/{ticker} se caía con 500 para TODOS los tickers
(la data estaba, el filtro era el roto).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from quant import pivot_points


def _serie_diaria(dias: int, hasta: datetime) -> list[dict]:
    """`dias` velas naive consecutivas terminando en `hasta` (inclusive)."""
    return [
        {"fecha": hasta - timedelta(days=i), "high": 101.0, "low": 99.0, "close": 100.0}
        for i in reversed(range(dias))
    ]


def test_los_4_timeframes_salen_con_niveles(monkeypatch):
    ayer = datetime.now(UTC).replace(tzinfo=None, hour=0, minute=0, second=0,
                                     microsecond=0) - timedelta(days=1)
    docs = _serie_diaria(400, ayer)
    monkeypatch.setattr(pivot_points, "_sql_docs_en_rango", lambda *a, **k: docs)

    res = pivot_points.obtener_4_timeframes("NVDA")

    assert res["last"] == 100.0
    for tf in ("diario", "semanal", "mensual", "anual"):
        assert res["frames"][tf] is not None, f"{tf} quedó sin data"
        assert res["frames"][tf]["levels"]["pp"] == 100.0


def test_diario_usa_una_sola_vela(monkeypatch):
    ayer = datetime.now(UTC).replace(tzinfo=None, hour=0, minute=0, second=0,
                                     microsecond=0) - timedelta(days=1)
    monkeypatch.setattr(pivot_points, "_sql_docs_en_rango",
                        lambda *a, **k: _serie_diaria(400, ayer))

    res = pivot_points.obtener_4_timeframes("NVDA")

    assert res["frames"]["diario"]["n_velas"] == 1
    assert res["frames"]["anual"]["n_velas"] > 1


def test_calcular_floor_trader():
    lv = pivot_points.calcular(high=110.0, low=90.0, close=100.0)
    assert lv["pp"] == 100.0
    assert lv["r1"] == 110.0
    assert lv["s1"] == 90.0
    assert lv["r2"] == 120.0
    assert lv["s2"] == 80.0
