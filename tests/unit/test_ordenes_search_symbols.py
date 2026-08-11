"""Tests del buscador de TICKER de OPERAR → TÍTULOS.

Nace del reporte "el TICKER no tiene resultados, nunca muestra nada"
(2026-08-11). El buscador leía `manager.pyrofex_instruments`, una tabla que
escribe UN one-shot MANUAL (`scripts/discovery_pyrofex.py`, que no está en el
crontab): si nadie lo corrió, la tabla está vacía y el combobox devuelve "sin
resultados" para cualquier ticker, sin error ni log. Ahora la fuente es el
universo LIVE del broker, y el SQL quedó de fallback.
"""
from __future__ import annotations

import pytest

from api.services import ordenes as mod


def _inst(ticker: str, *, cfi: str = "DBFXXX", underlying: str = "AL30") -> dict:
    return {"symbol": ticker, "cficode": cfi, "underlying": underlying,
            "currency": "ARS", "maturityDate": "20300109"}


UNIVERSO = [
    _inst("MERV - XMEV - AL30 - 24hs"),
    _inst("MERV - XMEV - AL30 - CI"),
    _inst("MERV - XMEV - AL30D - 24hs", underlying="AL30D"),
    _inst("MERV - XMEV - AL30C - 48hs", underlying="AL30C"),
    _inst("MERV - XMEV - GD30 - 24hs", underlying="GD30"),
    # Un FCI: NO puede aparecer en este buscador (tiene el suyo).
    _inst("ACAFCI", cfi="CIOIXX", underlying="ACA Renta Total"),
]


@pytest.fixture
def universo_live(monkeypatch):
    monkeypatch.setattr(mod, "_instruments_live", lambda: UNIVERSO)


def test_encuentra_el_ticker(universo_live):
    hits = mod.search_symbols("AL30")
    assert [h["ticker"] for h in hits], "el combobox no puede volver vacío con el papel en el universo"
    assert all("AL30" in h["ticker"] for h in hits)


def test_el_match_exacto_va_PRIMERO(universo_live):
    """El recorte a `limit` lo hace el backend: si no ordenara antes de cortar,
    buscar 'AL30' podía devolver AL30C/AL30D y dejar afuera el AL30."""
    hits = mod.search_symbols("AL30")
    assert hits[0]["ticker_corto"] == "AL30"
    assert hits[0]["ticker"].endswith(" - 24hs"), "a igual ticker, 24hs antes que CI"


def test_los_FCI_no_entran(universo_live):
    """Universo opuesto a search_fci. Un fondo pickeado acá entraría a
    adhoc_subscriptions y el motor nunca lo suscribiría."""
    hits = mod.search_symbols("ACA")
    assert hits == []


def test_devuelve_ticker_corto_ya_resuelto(universo_live):
    hits = mod.search_symbols("GD30")
    assert hits[0]["ticker_corto"] == "GD30"
    assert hits[0]["ticker"] == "MERV - XMEV - GD30 - 24hs"


def test_query_corta_no_pega_al_broker(monkeypatch):
    """Menos de 2 caracteres: se corta antes de traer 8600 instruments."""
    def _boom():
        raise AssertionError("no debería pedir el universo")
    monkeypatch.setattr(mod, "_instruments_live", _boom)
    assert mod.search_symbols("A") == []
    assert mod.search_symbols("") == []


def test_sin_universo_live_cae_al_SQL(monkeypatch):
    """Sesión pyRofex caída: antes de devolver vacío, servimos lo que haya en
    la tabla. Es el único caso en que el SQL sigue siendo la fuente."""
    monkeypatch.setattr(mod, "_instruments_live", list)
    llamado = {}
    monkeypatch.setattr(mod, "_search_symbols_sql",
                        lambda q, limit: llamado.setdefault("args", (q, limit)) or [])

    mod.search_symbols("AL30", limit=5)

    assert llamado["args"] == ("AL30", 5)


def test_respeta_el_limit(universo_live):
    assert len(mod.search_symbols("AL30", limit=2)) == 2


# ─── ticker_existe: validación previa a suscribir ────────────────────────────


def test_ticker_existe_usa_el_universo_live(universo_live):
    assert mod.ticker_existe("MERV - XMEV - AL30 - 24hs") is True
    assert mod.ticker_existe("MERV - XMEV - NOEXISTE - 24hs") is False


def test_ticker_vacio_no_existe(universo_live):
    assert mod.ticker_existe("") is False
