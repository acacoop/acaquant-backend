"""Tests unitarios de las tools Tier 2 (snapshot_historico, pendiente, liquidez).

Las 3 son funciones puras sobre el módulo `api.services.analitica` que
consultan Mongo. Acá mockeamos el acceso a Mongo (get_db_trading) para
validar la lógica sin DB real.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

import api.services.analitica as svc

# ─────────────────────────────────────────────────────────────────────────────
# Helpers para mockear Mongo
# ─────────────────────────────────────────────────────────────────────────────


class FakeCollection:
    """Collection stub que devuelve lo que se le programe para find/aggregate."""

    def __init__(self, find_docs=None, aggregate_docs=None, find_one=None):
        self._find_docs      = list(find_docs or [])
        self._aggregate_docs = list(aggregate_docs or [])
        self._find_one       = find_one

    def find(self, _filter=None, _projection=None):
        class _Cur:
            def __init__(self, docs):
                self._docs = docs

            def __iter__(self):
                return iter(self._docs)

            def sort(self, *a, **k):
                return self

            def limit(self, *a, **k):
                return self

        return _Cur(self._find_docs)

    def find_one(self, *a, **k):
        return self._find_one

    def aggregate(self, _pipeline):
        return iter(self._aggregate_docs)


def _mock_db(collections: dict):
    """Retorna un MagicMock que al hacer db[nombre] devuelve la collection.

    Colecciones NO programadas devuelven una FakeCollection vacía — espeja la
    semántica del patrón "live fallback" (ej. el service lee SnapshotsCierre
    primero y, si no hay docs, cae a MarketSnapshot): un test que no programa
    esa colección equivale a "no hay cierre persistido".
    """
    db = MagicMock()
    db.__getitem__.side_effect = lambda name: collections.get(name, FakeCollection())
    return db


@pytest.fixture(autouse=True)
def _clear_cache():
    """Vacía el cache de @cached entre tests para evitar cross-contamination."""
    from api.cache import clear_cache
    clear_cache()


# ─────────────────────────────────────────────────────────────────────────────
# snapshot_curva_historico
# ─────────────────────────────────────────────────────────────────────────────


def test_snapshot_historico_curva_invalida():
    assert svc.snapshot_curva_historico(curva="inexistente", fecha="2026-01-15") == []


def test_snapshot_historico_fecha_invalida(monkeypatch):
    monkeypatch.setattr(svc, "get_db_trading", lambda: _mock_db({
        "Curvas": FakeCollection(find_docs=[{"ticker": "TX26"}]),
    }))
    assert svc.snapshot_curva_historico(curva="cer", fecha="no-es-fecha") == []


def test_snapshot_historico_devuelve_bonos_con_trades_ese_dia(monkeypatch):
    """Caso base: 2 bonos CER, ambos operaron el 2026-01-15."""
    fecha_trade = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
    monkeypatch.setattr(svc, "get_db_trading", lambda: _mock_db({
        "Curvas": FakeCollection(find_docs=[
            {"ticker": "TICK1", "ticker_corto": "T1", "tipo": "cer",
             "fecha_vencimiento": "2027-06-15"},
            {"ticker": "TICK2", "ticker_corto": "T2", "tipo": "cer",
             "fecha_vencimiento": "2028-06-15"},
        ]),
        "TimeSales": FakeCollection(aggregate_docs=[
            {"_id": "TICK1", "price": 100.5, "TEA": 0.02, "TEM": 0.0017,
             "paridad": 99.5, "duration": 1.2, "convexity": 2.0, "ts": fecha_trade},
            {"_id": "TICK2", "price": 85.3, "TEA": 0.04, "TEM": 0.0033,
             "paridad": 90.2, "duration": 2.4, "convexity": 7.0, "ts": fecha_trade},
        ]),
    }))

    out = svc.snapshot_curva_historico(curva="cer", fecha="2026-01-15")
    assert len(out) == 2
    t1 = next(r for r in out if r["ticker"] == "TICK1")
    assert t1["ultimo_precio"] == 100.5
    assert t1["tea"] == 0.02
    assert t1["duration"] == 1.2
    assert t1["convexity"] == 2.0
    assert t1["fecha_vencimiento"] == "2027-06-15"


def test_snapshot_historico_excluye_bonos_que_no_operaron(monkeypatch):
    """Si un bono NO tiene trade ese día, no aparece en el resultado."""
    fecha_trade = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
    monkeypatch.setattr(svc, "get_db_trading", lambda: _mock_db({
        "Curvas": FakeCollection(find_docs=[
            {"ticker": "OPERA",    "ticker_corto": "OP", "fecha_vencimiento": "2027-01-01"},
            {"ticker": "NO_OPERA", "ticker_corto": "NO", "fecha_vencimiento": "2027-01-01"},
        ]),
        "TimeSales": FakeCollection(aggregate_docs=[
            {"_id": "OPERA", "price": 100, "TEA": 0.01, "duration": 1.0, "ts": fecha_trade},
        ]),
    }))

    out = svc.snapshot_curva_historico(curva="cer", fecha="2026-01-15")
    tickers = {r["ticker"] for r in out}
    assert "OPERA" in tickers
    assert "NO_OPERA" not in tickers


# ─────────────────────────────────────────────────────────────────────────────
# calcular_pendiente_curva
# ─────────────────────────────────────────────────────────────────────────────


def test_pendiente_curva_invalida():
    out = svc.calcular_pendiente_curva(curva="inexistente")
    assert "error" in out


def test_pendiente_metrica_invalida():
    out = svc.calcular_pendiente_curva(curva="cer", metrica="rsi")
    assert "error" in out


def test_pendiente_actual_largo_menos_corto(monkeypatch):
    """TEA largo 0.05 - corto 0.02 = 300 bps."""
    # Mock listar_curva para evitar el pipeline real
    fake_curva = [
        {"ticker": "T1", "ticker_corto": "T1", "tea": 0.02, "duration": 0.5,
         "meses_al_vto": 6.0},
        {"ticker": "T2", "ticker_corto": "T2", "tea": 0.03, "duration": 1.5,
         "meses_al_vto": 18.0},
        {"ticker": "T3", "ticker_corto": "T3", "tea": 0.05, "duration": 3.0,
         "meses_al_vto": 36.0},
    ]
    monkeypatch.setattr(svc, "listar_curva", lambda **kw: fake_curva)

    out = svc.calcular_pendiente_curva(curva="cer", metrica="tea")
    assert out["pendiente_actual_bps"] == 300  # (0.05 - 0.02) * 10000
    assert out["corto"]["ticker"] == "T1"
    assert out["largo"]["ticker"] == "T3"
    assert out["metrica"] == "tea"


def test_pendiente_con_fecha_comparacion_empinamiento(monkeypatch):
    """Hoy pendiente 300 bps, hace 30 días 200 bps → empinamiento."""
    monkeypatch.setattr(svc, "listar_curva", lambda **kw: [
        {"ticker": "T1", "ticker_corto": "T1", "tea": 0.02, "duration": 0.5,
         "meses_al_vto": 6.0},
        {"ticker": "T2", "ticker_corto": "T2", "tea": 0.05, "duration": 3.0,
         "meses_al_vto": 36.0},
    ])
    monkeypatch.setattr(svc, "snapshot_curva_historico", lambda **kw: [
        {"ticker": "T1", "ticker_corto": "T1", "tea": 0.03, "duration": 0.5},
        {"ticker": "T2", "ticker_corto": "T2", "tea": 0.05, "duration": 3.0},
    ])

    out = svc.calcular_pendiente_curva(
        curva="cer", metrica="tea", fecha_comparacion="2025-12-15",
    )
    assert out["pendiente_actual_bps"]     == 300
    assert out["pendiente_comparacion_bps"] == 200
    assert out["delta_bps"]                 == 100
    assert out["interpretacion"]            == "empinamiento"


def test_pendiente_aplanamiento(monkeypatch):
    monkeypatch.setattr(svc, "listar_curva", lambda **kw: [
        {"ticker": "T1", "tea": 0.03, "duration": 0.5, "meses_al_vto": 6.0},
        {"ticker": "T2", "tea": 0.04, "duration": 3.0, "meses_al_vto": 36.0},
    ])
    monkeypatch.setattr(svc, "snapshot_curva_historico", lambda **kw: [
        {"ticker": "T1", "tea": 0.02, "duration": 0.5},
        {"ticker": "T2", "tea": 0.05, "duration": 3.0},
    ])
    out = svc.calcular_pendiente_curva(curva="cer", fecha_comparacion="2025-12-15")
    # Actual 100 bps, historical 300 bps → delta -200
    assert out["delta_bps"] == -200
    assert out["interpretacion"] == "aplanamiento"


def test_pendiente_sin_datos_suficientes(monkeypatch):
    """Solo 1 bono en la curva — no se puede calcular pendiente."""
    monkeypatch.setattr(svc, "listar_curva", lambda **kw: [
        {"ticker": "T1", "tea": 0.02, "duration": 1.0},
    ])
    out = svc.calcular_pendiente_curva(curva="cer")
    assert "error" in out


# (tests de liquidez_secundario eliminados 2026-06-22 — la función se borró: leía
# TimeSales 20d, solo se exponía por MCP sin uso en el front.)
