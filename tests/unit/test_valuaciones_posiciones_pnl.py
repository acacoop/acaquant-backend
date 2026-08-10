"""Merge de cost-basis sobre las posiciones (api/services/valuaciones_sql._enriquecer_con_pnl).

Es la regla que alimenta las columnas COSTO / PNL / GAN % del PORTFOLIO en CARTERAS.
"""
from __future__ import annotations

import pytest

from api.services import valuaciones_sql as vs


def _resp(fecha: str, unidades: list[str]) -> dict:
    return {
        "id_cuenta": "100",
        "fecha": fecha,
        "posiciones": [
            {"unidad": u, "costo": None, "pnl": None, "gan_pct": None} for u in unidades
        ],
        "pnl_disponible": False,
        "costo_total": 0.0,
        "pnl_total": 0.0,
        "pnl_detalle": {},
    }


@pytest.fixture
def motor(monkeypatch):
    """Última fecha = 2026-08-10; el motor devuelve una posición con boletos."""
    monkeypatch.setattr(vs, "_resolver_fecha", lambda *a, **k: "2026-08-10")
    rows = [{
        "unidad": "AL30", "ticker": "AL30",
        "costo_remanente": 1000.0,
        "pnl_no_realizado": 200.0,
        "pnl_pasivo": 50.0,
        "pnl_realizado": 9999.0,   # NO entra al total mostrado
        "boletos": [{"fecha": "2026-01-02"}],
    }]
    from api.services import pnl_sql
    monkeypatch.setattr(pnl_sql, "pnl_por_cuenta_sql", lambda **k: {"rows": rows})
    return rows


def test_merge_por_unidad_calcula_pnl_y_gan(motor):
    r = _resp("2026-08-10", ["AL30", "GD30"])
    vs._enriquecer_con_pnl(r, "100")

    al30, gd30 = r["posiciones"]
    # PnL mostrado = no realizado + cobros pasivos (el realizado queda afuera).
    assert (al30["costo"], al30["pnl"], al30["gan_pct"]) == (1000.0, 250.0, 25.0)
    # Sin match en el motor (sin boletos) → sin números inventados.
    assert (gd30["costo"], gd30["pnl"], gd30["gan_pct"]) == (None, None, None)

    assert r["pnl_disponible"] is True
    assert (r["costo_total"], r["pnl_total"]) == (1000.0, 250.0)
    assert r["pnl_detalle"]["AL30"]["boletos"]


def test_fecha_historica_no_se_cruza_con_el_pnl_de_hoy(motor):
    r = _resp("2026-03-31", ["AL30"])
    vs._enriquecer_con_pnl(r, "100")

    assert r["pnl_disponible"] is False
    assert r["posiciones"][0]["pnl"] is None
    assert r["pnl_detalle"] == {}


def test_motor_caido_devuelve_las_posiciones_igual(monkeypatch):
    monkeypatch.setattr(vs, "_resolver_fecha", lambda *a, **k: "2026-08-10")
    from api.services import pnl_sql

    def _boom(**k):
        raise RuntimeError("statement timeout")

    monkeypatch.setattr(pnl_sql, "pnl_por_cuenta_sql", _boom)

    r = _resp("2026-08-10", ["AL30"])
    vs._enriquecer_con_pnl(r, "100")

    assert r["pnl_disponible"] is False
    assert r["posiciones"][0]["costo"] is None
