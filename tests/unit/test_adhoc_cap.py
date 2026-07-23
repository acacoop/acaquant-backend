"""Tests del cupo de suscripciones adhoc (core/adhoc_subscriptions.subscribe).

Lo crítico: cuando el cupo está lleno, hacer lugar desalojando la fila MENOS
usada — pero NUNCA una que alguien esté mirando en vivo. Nace del bug real
(2026-07-23): el cupo se llenaba de tickers de días atrás y rechazaba el nuevo,
así que la card no traía nada aunque el mercado estuviera abierto.

La DB se simula con un cursor falso que entiende las 4 consultas del subscribe.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core import adhoc_subscriptions as ah


class _FakeDB:
    """Estado en memoria: {ticker: last_used_at}. Todas se consideran vivas."""

    def __init__(self, filas: dict[str, datetime]):
        self.filas = dict(filas)
        self.borrados: list[str] = []

    # ── protocolo de cursor/conexión que usa el módulo ──
    def cursor(self):
        return _FakeCursor(self)

    def connection(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeCursor:
    def __init__(self, db: _FakeDB):
        self.db = db
        self._res = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        s = " ".join(sql.split()).lower()
        if s.startswith("select 1 from") and "where ticker = %s" in s:
            self._res = (1,) if params[0] in self.db.filas else None
        elif s.startswith("update") and "where ticker = %s" in s:
            self.rowcount = 1 if params[-1] in self.db.filas else 0
        elif s.startswith("select count(*)"):
            self._res = (len(self.db.filas),)
        elif s.startswith("delete") and "order by last_used_at asc" in s:
            # desalojo LRU: la más vieja con last_used_at < corte
            corte = params[0]
            candidatas = [(t, ts) for t, ts in self.db.filas.items() if ts < corte]
            if candidatas:
                victima = min(candidatas, key=lambda x: x[1])[0]
                del self.db.filas[victima]
                self.db.borrados.append(victima)
                self.rowcount = 1
            else:
                self.rowcount = 0
        elif s.startswith("insert"):
            self.db.filas[params[0]] = params[2]   # last_used_at
            self.rowcount = 1
        else:
            self._res = None

    def fetchone(self):
        return self._res


@pytest.fixture
def db(monkeypatch):
    holder = {}

    def _instalar(filas):
        holder["db"] = _FakeDB(filas)
        monkeypatch.setattr(ah, "get_pool", lambda: holder["db"])
        return holder["db"]

    return _instalar


def _viejas(n: int) -> dict[str, datetime]:
    """n filas 'de ayer' — todas desalojables."""
    base = datetime.now(UTC) - timedelta(hours=16)
    return {f"T{i}": base + timedelta(seconds=i) for i in range(n)}


def test_cupo_lleno_de_basura_desaloja_la_mas_vieja(db):
    """El caso del bug: 50 tickers viejos. Pedir uno nuevo NO puede dar 429 —
    tiene que tirar el más viejo y entrar."""
    fake = db(_viejas(ah.CAP))
    r = ah.subscribe("NUEVO")
    assert r["ok"] and r["created"]
    assert fake.borrados == ["T0"]              # el de last_used_at más viejo
    assert "NUEVO" in fake.filas and len(fake.filas) == ah.CAP


def test_no_desaloja_una_card_abierta(db):
    """Todas las CAP filas se tocaron hace un segundo (50 cards en vivo): NO se
    puede tirar ninguna, así que ahí sí corresponde 429."""
    ahora = datetime.now(UTC)
    fake = db({f"T{i}": ahora - timedelta(seconds=1) for i in range(ah.CAP)})
    r = ah.subscribe("NUEVO")
    assert r["ok"] is False and r["reason"] == "cap"
    assert fake.borrados == [] and "NUEVO" not in fake.filas


def test_mezcla_desaloja_solo_la_ociosa(db):
    """49 en vivo + 1 ociosa: entra la nueva tirando SOLO la ociosa, ni una de
    las que se están mirando."""
    ahora = datetime.now(UTC)
    filas = {f"T{i}": ahora - timedelta(seconds=1) for i in range(ah.CAP - 1)}
    filas["OCIOSA"] = ahora - timedelta(minutes=30)
    fake = db(filas)
    r = ah.subscribe("NUEVO")
    assert r["ok"] and fake.borrados == ["OCIOSA"]


def test_por_debajo_del_cupo_no_desaloja_nada(db):
    fake = db(_viejas(10))
    r = ah.subscribe("NUEVO")
    assert r["ok"] and fake.borrados == [] and len(fake.filas) == 11


def test_ticker_existente_solo_refresca(db):
    fake = db(_viejas(ah.CAP))
    r = ah.subscribe("T5")
    assert r["ok"] and r["created"] is False
    assert fake.borrados == [] and len(fake.filas) == ah.CAP


def test_ticker_vacio_se_rechaza(db):
    db({})
    assert ah.subscribe("   ")["ok"] is False
