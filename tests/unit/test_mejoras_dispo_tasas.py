"""Mejoras Dispo (Agro) — la columna TNA es TNA, no la TEA.

Congela el bug de 2026-08-26: `_build_filas` publicaba en `tna` el valor crudo
de `mercado.market_snapshot`, que es la **TEA**. No fallaba nada — la columna
simplemente mostraba otro número que el que el mismo bono muestra en RENTA
FIJA. Un test es la única forma de que no vuelva: las dos tasas son floats
plausibles y a ojo no se distinguen.
"""
from datetime import date, timedelta

import pytest

from api.services.mejoras_dispo import _build_filas
from quant.tasas import rendimiento_al_plazo, tna_desde_tea

HOY = date(2026, 8, 26)
TEA = 0.2934


def _filas(dias: int = 180, precio: float | None = 300_000.0):
    vto = HOY + timedelta(days=dias)
    lecaps = [{"ticker": "MERV - XMEV - S30S6 - 24hs", "ticker_corto": "S30S6",
               "fecha_vencimiento": vto.isoformat(), "flujo_vencimiento": 130.0}]
    return _build_filas(lecaps, {"MERV - XMEV - S30S6 - 24hs": TEA}, {}, precio, HOY)


def test_tna_no_es_la_tea():
    f = _filas()[0]
    assert f["tea"] == pytest.approx(TEA)
    assert f["tna"] == pytest.approx(tna_desde_tea(TEA))
    # El bug era exactamente esta igualdad.
    assert f["tna"] != pytest.approx(TEA)
    assert f["tna"] < f["tea"]


def test_tna_es_la_misma_convencion_que_renta_fija():
    """TEM×12 — la fórmula que derivan `renta-fija-table.tsx` y `bonos-table.tsx`.
    Si acá cambiara, el mismo papel mostraría dos TNAs según la pantalla."""
    tem = (1 + TEA) ** (1 / 12) - 1
    assert _filas()[0]["tna"] == pytest.approx(tem * 12)


def test_tasa_directa_prorratea_la_tna_lineal():
    f = _filas(dias=180)[0]
    assert f["tasa_directa"] == pytest.approx(f["tna"] * 180 / 365)
    # Y el interés sale de ESA tasa, no de la TEA linealizada (lo viejo).
    assert f["interes_ganado"] == pytest.approx(300_000.0 * f["tasa_directa"])
    assert f["valor_final"] == pytest.approx(300_000.0 + f["interes_ganado"])


def test_rendimiento_efectivo_es_la_plata_real_al_vto():
    f = _filas(dias=180)[0]
    assert f["rendimiento_efectivo"] == pytest.approx(rendimiento_al_plazo(TEA, 180))
    # Convención lineal ≠ capitalizado: son dos números distintos a propósito.
    assert f["rendimiento_efectivo"] > f["tasa_directa"]


def test_sin_tea_la_fila_igual_aparece_con_todo_en_none():
    """El trader tiene que ver el universo completo de Lecaps, con o sin tasa."""
    vto = HOY + timedelta(days=90)
    lecaps = [{"ticker": "MERV - XMEV - XXXX - 24hs", "ticker_corto": "XXXX",
               "fecha_vencimiento": vto.isoformat(), "flujo_vencimiento": 130.0}]
    f = _build_filas(lecaps, {}, {}, 300_000.0, HOY)[0]
    assert f["ticker"] == "XXXX" and f["dias"] == 90
    for k in ("tea", "tna", "rendimiento_efectivo", "tasa_directa", "interes_ganado"):
        assert f[k] is None
