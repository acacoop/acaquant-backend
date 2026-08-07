"""Auditoría de DÍAS SIN OPERAR (`comercial_sql.detalle_ultima_op`).

Congela lo que el modal de la tabla ESTADO COMERCIAL tiene que poder mostrar:
CUÁL boleto fija el número, y cuáles NO cuentan y por qué. Sin DB: se
monkeypatchea `_q` y se despacha por el SQL de cada paso.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from api.services import comercial_sql as cs

CORTE = "2026-08-07"


def _fila(boleto: str, dia: date, *, anulado: datetime | None = None, **kw) -> dict:
    base = {
        "boleto": boleto, "concertacion": dia, "operacion": "Compra",
        "tipo_operacion": "Contado - Compra", "instrumento": "AL30",
        "mercado": "BYMA", "moneda": "ARS", "bruto": 1000, "arancel": 5,
        "cantidad": 10, "etapa": None, "es_cierre": False,
        "condiciones": "ARS 24hs", "anulado_en": anulado, "ingestado_en": None,
    }
    base.update(kw)
    return base


def _mock_q(monkeypatch, *, ult, boletos, posteriores=()):
    """Despacha por el SQL: cabecera / última op / boletos / posteriores."""
    def fake(sql: str, params=None):
        if "FROM cuentas u" in sql:
            return [{"denominacion": "[101] ASOCIACION", "operador_email": "a@b.com",
                     "operador_nombre": "Ana", "nivel_1": "PRODUCTORES", "nivel_3": None,
                     "estado": "Activa", "fecha_alta_legajo": date(2020, 1, 1)}]
        if "max(concertacion)" in sql:
            return [{"ult": ult}]
        if "count(*) OVER()" in sql:
            return [{**r, "n_total": len(posteriores)} for r in posteriores]
        if "concertacion = %(ult)s" in sql:
            return [r for r in boletos if r["concertacion"] == ult]
        return sorted(boletos, key=lambda r: r["concertacion"], reverse=True)
    monkeypatch.setattr(cs, "_q", fake)


def test_marca_el_boleto_que_fija_los_dias(monkeypatch):
    ult = date(2026, 8, 6)
    _mock_q(monkeypatch, ult=ult, boletos=[
        _fila("B-2", ult), _fila("B-1", date(2026, 5, 2)),
    ])
    d = cs.detalle_ultima_op(id_cuenta="101", fecha=CORTE)

    assert d["ultima_op"] == "2026-08-06"
    assert d["dias_sin_operar"] == 1
    assert d["estado"] == "ACTIVA"
    assert d["n_boletos_ultima_fecha"] == 1
    ultima = [i for i in d["items"] if i["es_ultima"]]
    assert [i["boleto"] for i in ultima] == ["B-2"]
    # El historial viene del más nuevo al más viejo.
    assert [i["boleto"] for i in d["items"]] == ["B-2", "B-1"]


def test_anulado_no_cuenta_y_dice_por_que(monkeypatch):
    """El caso que motiva el modal: la mesa recuerda una op reciente, pero el

    boleto está anulado → no mueve los días, y hay que poder VERLO."""
    ult = date(2026, 6, 1)
    _mock_q(monkeypatch, ult=ult, boletos=[
        _fila("ANU", date(2026, 8, 6), anulado=datetime(2026, 8, 6, 15, 0)),
        _fila("OK", ult),
    ])
    d = cs.detalle_ultima_op(id_cuenta="101", fecha=CORTE)

    assert d["ultima_op"] == "2026-06-01"      # el anulado NO adelantó la fecha
    assert d["dias_sin_operar"] == 67
    assert d["estado"] == "ENFRIANDOSE"
    anulado = next(i for i in d["items"] if i["boleto"] == "ANU")
    assert anulado["excluido"] and not anulado["es_ultima"]
    assert "anulado el 06/08/2026" in anulado["observacion"]
    assert d["n_excluidos"] == 1


def test_varios_boletos_el_mismo_dia_se_muestran_todos(monkeypatch):
    ult = date(2026, 8, 6)
    _mock_q(monkeypatch, ult=ult, boletos=[_fila("B-2", ult), _fila("B-3", ult)])
    d = cs.detalle_ultima_op(id_cuenta="101", fecha=CORTE)
    assert d["n_boletos_ultima_fecha"] == 2


def test_posteriores_al_corte_no_cuentan_en_la_foto(monkeypatch):
    """Modo foto: los boletos después del corte explican por qué la foto

    muestra más días que la vista de hoy — se listan, marcados."""
    ult = date(2026, 5, 1)
    _mock_q(monkeypatch, ult=ult, boletos=[_fila("VIEJO", ult)],
            posteriores=[_fila("NUEVO", date(2026, 8, 20))])
    d = cs.detalle_ultima_op(id_cuenta="101", fecha=CORTE)

    assert d["es_foto"] is True
    assert d["n_posteriores_corte"] == 1
    post = next(i for i in d["items"] if i["boleto"] == "NUEVO")
    assert post["excluido"]
    assert "posterior al corte 07/08/2026" in post["observacion"]


def test_cuenta_sin_boletos_es_nueva(monkeypatch):
    _mock_q(monkeypatch, ult=None, boletos=[])
    d = cs.detalle_ultima_op(id_cuenta="101", fecha=CORTE)

    assert d["estado"] == "NUEVA"
    assert d["ultima_op"] is None
    assert d["dias_sin_operar"] is None
    assert d["items"] == []
    assert "nunca operó" in d["ecuacion"]


def test_dormida_cuando_la_ultima_op_quedo_fuera_de_ventana(monkeypatch):
    ult = date(2025, 1, 2)
    _mock_q(monkeypatch, ult=ult, boletos=[_fila("VIEJO", ult)])
    d = cs.detalle_ultima_op(id_cuenta="101", fecha=CORTE)
    assert d["estado"] == "DORMIDA"
    assert d["dias_sin_operar"] == (date(2026, 8, 7) - ult).days


@pytest.mark.parametrize("dias,esperado", [(0, "ACTIVA"), (45, "ACTIVA"), (46, "ENFRIANDOSE")])
def test_umbral_activa_igual_que_la_tabla(monkeypatch, dias, esperado):
    """El modal no puede contradecir a la tabla: mismos umbrales (45/90)."""
    ult = date.fromisoformat(CORTE) - __import__("datetime").timedelta(days=dias)
    _mock_q(monkeypatch, ult=ult, boletos=[_fila("X", ult)])
    d = cs.detalle_ultima_op(id_cuenta="101", fecha=CORTE)
    assert d["estado"] == esperado
    assert d["umbrales"] == {"activa": 45, "dormida": 90}
