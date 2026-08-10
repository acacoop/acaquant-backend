"""Volumen DOLARIZADO (`moneda=USD_DOL`) en la vista OPERACIONES.

Regresión 2026-08-10: los boletos ARS sin snapshot `mep` (los que escribe
jobs/fci_bilateral) se sumaban como CERO — el mercado FCI Bilateral mostraba
volumen ARS real y ~0 al dolarizar. Ahora la expresión cae al MEP del día.

La matemática se valida con sqlite reemplazando la subquery de `valuaciones.dolar`
(que usa `AT TIME ZONE`, sin equivalente en sqlite) por una equivalente sobre una
tabla `dolar(dia, mep)` — la SEMÁNTICA que se testea es la misma: último TC con
fecha <= concertación.
"""
from __future__ import annotations

import sqlite3

import pytest

from api.services.operaciones_sql import _MEP_DIA, _bruto_expr

_MEP_DIA_SQLITE = ("(SELECT d.mep FROM dolar d WHERE d.mep > 0 "
                   "AND d.dia <= operaciones.concertacion ORDER BY d.dia DESC LIMIT 1)")

# (concertacion, moneda, bruto, mep)
_DOLAR = [("2026-07-01", 1000.0), ("2026-07-15", 1250.0)]


def _sum_usd_dol(filas):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE operaciones (concertacion TEXT, moneda TEXT, bruto REAL, mep REAL)")
    db.execute("CREATE TABLE dolar (dia TEXT, mep REAL)")
    db.executemany("INSERT INTO operaciones VALUES (?,?,?,?)", filas)
    db.executemany("INSERT INTO dolar VALUES (?,?)", _DOLAR)
    expr = _bruto_expr("USD_DOL").replace(_MEP_DIA, _MEP_DIA_SQLITE)
    return db.execute(f"SELECT {expr} FROM operaciones").fetchone()[0]


def test_boleto_ars_sin_mep_usa_el_tc_del_dia():
    """El caso FCI Bilateral: sin `mep` propio ya NO vale cero."""
    assert _sum_usd_dol([("2026-07-20", "ARS", 1_250_000.0, None)]) == pytest.approx(1000.0)


def test_snapshot_del_boleto_gana_sobre_el_del_dia():
    assert _sum_usd_dol([("2026-07-20", "ARS", 1_250_000.0, 2500.0)]) == pytest.approx(500.0)


def test_usd_nativo_no_se_convierte():
    assert _sum_usd_dol([("2026-07-20", "USD", 700.0, None)]) == pytest.approx(700.0)


def test_mezcla_ars_sin_mep_mas_usd():
    filas = [("2026-07-20", "ARS", 1_250_000.0, None), ("2026-07-20", "USD", 700.0, None)]
    assert _sum_usd_dol(filas) == pytest.approx(1700.0)


def test_fecha_anterior_al_feed_no_rompe():
    """Sin TC en ninguna parte → la fila no aporta (NULL), no explota ni miente."""
    assert _sum_usd_dol([("2020-01-02", "ARS", 1_000.0, None)]) is None
