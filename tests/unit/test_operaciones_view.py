"""Tests de api/services/operaciones_view.py — los helpers PUROS que quedan
(parser de fecha de Movimientos). Los constructores de match Mongo
(ops_match/arancel_match), los cuerpos /ops/* y el selector `motor()` (dual-run
SQL/Mongo) se ELIMINARON al cutover SQL-native de Operaciones (REGLA #1 19/6:
Mongo se apaga, el código muerto se borra)."""
from __future__ import annotations

import api.services.operaciones_view as ov

# ── ddmmyyyy_a_iso ───────────────────────────────────────────────────────────

def test_ddmmyyyy_a_iso():
    assert ov.ddmmyyyy_a_iso("02/07/2025") == "2025-07-02"
    assert ov.ddmmyyyy_a_iso(" 31/12/2024 ") == "2024-12-31"
    assert ov.ddmmyyyy_a_iso("no-fecha") is None
    assert ov.ddmmyyyy_a_iso(None) is None
