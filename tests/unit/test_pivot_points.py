"""Tests unit de quant/pivot_points.py — sin DB: el cálculo RECIBE las velas.

Congela dos cosas:
  · el fix del 2026-08-10: los rangos son AWARE (UTC) y `fecha` de las velas es
    NAIVE; compararlos como datetime tiraba TypeError y /api/scanner/pivot se
    caía con 500 para TODOS los tickers.
  · el golden del refactor que sacó SQL de `quant/`: mismos niveles antes y
    después con la misma serie (medido con el código viejo, mock de la query).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from quant import pivot_points


def _ayer() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, hour=0, minute=0, second=0,
                                     microsecond=0) - timedelta(days=1)


def _serie_diaria(dias: int, hasta: datetime) -> list[dict]:
    """`dias` velas naive consecutivas terminando en `hasta` (inclusive)."""
    return [
        {"fecha": hasta - timedelta(days=i), "high": 101.0, "low": 99.0, "close": 100.0}
        for i in reversed(range(dias))
    ]


def _serie_variada(dias: int, hasta: datetime) -> list[dict]:
    """Velas con H/L/C que ciclan (7/5/3): en cualquier ventana de ≥7 velas el
    máximo es 106 y el mínimo 86; la última vela (k=dias) fija el diario."""
    out = []
    for i in reversed(range(dias)):
        k = dias - i
        out.append({"fecha": hasta - timedelta(days=i),
                    "high": 100 + (k % 7), "low": 90 - (k % 5), "close": 95 + (k % 3)})
    return out


def test_los_4_timeframes_salen_con_niveles():
    res = pivot_points.obtener_4_timeframes("NVDA", _serie_diaria(400, _ayer()))

    assert res["last"] == 100.0
    for tf in ("diario", "semanal", "mensual", "anual"):
        assert res["frames"][tf] is not None, f"{tf} quedó sin data"
        assert res["frames"][tf]["levels"]["pp"] == 100.0


def test_diario_usa_una_sola_vela():
    res = pivot_points.obtener_4_timeframes("NVDA", _serie_diaria(400, _ayer()))

    assert res["frames"]["diario"]["n_velas"] == 1
    assert res["frames"]["anual"]["n_velas"] > 1


def test_golden_mismos_niveles_que_antes_del_refactor():
    # Números medidos con el código que leía SQL (mock de la query) sobre esta
    # misma serie: 400 velas, k=400 → H=101, L=90, C=96 en la última.
    res = pivot_points.obtener_4_timeframes("NVDA", _serie_variada(400, _ayer()))

    assert res["last"] == 96
    d = res["frames"]["diario"]
    assert (d["n_velas"], d["h"], d["l"], d["c"]) == (1, 101, 90, 96)
    lv = {k: round(v, 4) for k, v in d["levels"].items()}
    assert lv == {"pp": 95.6667, "r1": 101.3333, "s1": 90.3333, "r2": 106.6667,
                  "s2": 84.6667, "r3": 112.3333, "s3": 79.3333}
    for tf in ("semanal", "mensual", "anual"):
        assert (res["frames"][tf]["h"], res["frames"][tf]["l"]) == (106, 86), tf


def test_debug_da_los_mismos_niveles_que_el_calculo():
    velas = _serie_variada(400, _ayer())
    res = pivot_points.obtener_4_timeframes("NVDA", velas)
    dbg = pivot_points.debug_4_timeframes("NVDA", velas)

    assert dbg["last"] == res["last"]
    for tf in ("diario", "semanal", "mensual", "anual"):
        assert dbg["frames"][tf]["ok"], tf
        assert dbg["frames"][tf]["levels"] == res["frames"][tf]["levels"], tf
        assert dbg["frames"][tf]["n_velas"] == res["frames"][tf]["n_velas"], tf


def test_sin_velas_cae_a_la_ultima_historica():
    ultima = {"fecha": datetime(2024, 3, 1), "close": 12.5}
    res = pivot_points.obtener_4_timeframes("XXXX", [], ultima)
    assert res["last"] == 12.5
    assert all(v is None for v in res["frames"].values())
    assert pivot_points.obtener_4_timeframes("XXXX", [])["last"] is None


def test_ventana_lectura_cubre_del_anio_previo_a_manana():
    desde, hasta = pivot_points.ventana_lectura()
    hoy = datetime.now(UTC).date()
    assert desde == hoy.replace(year=hoy.year - 1, month=1, day=1)
    assert hasta == hoy + timedelta(days=1)


def test_calcular_floor_trader():
    lv = pivot_points.calcular(high=110.0, low=90.0, close=100.0)
    assert lv["pp"] == 100.0
    assert lv["r1"] == 110.0
    assert lv["s1"] == 90.0
    assert lv["r2"] == 120.0
    assert lv["s2"] == 80.0
