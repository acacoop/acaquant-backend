"""Tests de api/services/operaciones_view.py — los helpers PUROS que quedan
(selector de motor + parser de fecha de Movimientos). Los constructores de match
Mongo (ops_match/arancel_match) y los cuerpos /ops/* se ELIMINARON al cutover
SQL-native de Operaciones (REGLA #1 19/6: Mongo se apaga, el código muerto se borra)."""
from __future__ import annotations

import api.services.operaciones_view as ov

# ── motor (selector SQL/Mongo) ───────────────────────────────────────────────

def test_motor_override_explicito_gana():
    assert ov.motor("sql") == "sql"
    assert ov.motor("mongo") == "mongo"


def test_motor_default_mongo_si_flag_off(monkeypatch):
    monkeypatch.delenv("OPERACIONES_SQL", raising=False)
    assert ov.motor(None) == "mongo"


def test_motor_sql_si_flag_on(monkeypatch):
    monkeypatch.setenv("OPERACIONES_SQL", "1")
    assert ov.motor(None) == "sql"
    # valor inválido en el query → cae al flag, no rompe
    assert ov.motor("xxx") == "sql"


# ── ddmmyyyy_a_iso ───────────────────────────────────────────────────────────

def test_ddmmyyyy_a_iso():
    assert ov.ddmmyyyy_a_iso("02/07/2025") == "2025-07-02"
    assert ov.ddmmyyyy_a_iso(" 31/12/2024 ") == "2024-12-31"
    assert ov.ddmmyyyy_a_iso("no-fecha") is None
    assert ov.ddmmyyyy_a_iso(None) is None
