"""Candado del guard de fecha de cashflow_sql (hallazgo /ia-review, sin cubrir).

v1.91 movió el filtro de rango de Python a SQL en listar_flujos. Para que
`to_date(fecha,'DD/MM/YYYY')` no reviente con una fila `movimientos.fecha`
malformada, hay un guard regex que descarta las fechas basura ANTES del to_date.
Si alguien lo saca, un movimiento con fecha basura tira una EXCEPCIÓN SQL en una
query del chat (flujo_de_fondos / serie_historica), en el pool que sirve la web
— no un 'sin datos'. Este test congela que el guard esté cuando hay rango.

SQL interceptado: no toca la DB.
"""
from __future__ import annotations

from api.services import cashflow_sql


def test_el_guard_de_fecha_esta_cuando_hay_rango(monkeypatch):
    visto = {}
    monkeypatch.setattr(cashflow_sql, "_q",
                        lambda sql, p=None: visto.update(sql=sql, p=p or {}) or [])
    cashflow_sql.listar_flujos(desde="2026-06-01", hasta="2026-06-30")
    sql = " ".join(visto["sql"].split())
    # el guard: solo pasa al to_date lo que matchea dd/mm/yyyy
    assert "fecha ~ '^[0-9]" in sql
    assert "to_date(fecha, 'DD/MM/YYYY')" in sql


def test_sin_rango_no_hay_to_date(monkeypatch):
    """Sin fechas no se arma el to_date — no hay nada que blindar."""
    visto = {}
    monkeypatch.setattr(cashflow_sql, "_q",
                        lambda sql, p=None: visto.update(sql=sql) or [])
    cashflow_sql.listar_flujos()
    assert "to_date" not in visto["sql"]
