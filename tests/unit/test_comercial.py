"""Tests del estado comercial (api/services/comercial.py).

Congela el semáforo de actividad: NUEVA / ACTIVA / ENFRIANDOSE / DORMIDA
según días sin operar (umbrales 30/90 por default) + el $match de volumen
(que debe filtrar por id_cuenta indexado, NO por regex sobre cuenta).
"""
from __future__ import annotations

from api.services.comercial import _match_volumen, estado_comercial
from jobs.negocio_movimientos import _extract_id_cuenta


def test_nunca_opero_es_nueva():
    assert estado_comercial(None, opero_alguna_vez=False, dias_activa=30, dias_dormida=90) == "NUEVA"


def test_opero_pero_fuera_de_ventana_es_dormida():
    # dias=None (no está en la ventana reciente) + operó alguna vez → DORMIDA.
    assert estado_comercial(None, opero_alguna_vez=True, dias_activa=30, dias_dormida=90) == "DORMIDA"


def test_opero_reciente_es_activa():
    assert estado_comercial(10, opero_alguna_vez=True, dias_activa=30, dias_dormida=90) == "ACTIVA"


def test_borde_activa_inclusive():
    assert estado_comercial(30, opero_alguna_vez=True, dias_activa=30, dias_dormida=90) == "ACTIVA"


def test_entre_umbrales_es_enfriandose():
    assert estado_comercial(60, opero_alguna_vez=True, dias_activa=30, dias_dormida=90) == "ENFRIANDOSE"


# ── id_cuenta denormalizado (ingesta) ──────────────────────────────────────

def test_extract_id_cuenta():
    assert _extract_id_cuenta("[805] MOLLO NICOLAS") == "805"
    assert _extract_id_cuenta("[1114] OTRA CUENTA SA") == "1114"
    assert _extract_id_cuenta("sin corchete") is None
    assert _extract_id_cuenta("") is None
    assert _extract_id_cuenta(None) is None


# ── _match_volumen filtra por id_cuenta indexado (no regex) ─────────────────

def test_match_volumen_usa_id_cuenta():
    m = _match_volumen(("805", "112"), "ARS", "2026-01-01")
    assert m["id_cuenta"] == {"$in": ["805", "112"]}
    assert m["moneda"] == "ARS"
    assert "$in" in m["categoria"]
    assert m["fecha"] == {"$gte": "2026-01-01"}
    # NO debe filtrar por regex sobre `cuenta` (eso es lo que no escalaba).
    assert "cuenta" not in m


def test_match_volumen_sin_fecha():
    m = _match_volumen(("805",), "USD", None)
    assert "fecha" not in m
    assert m["id_cuenta"] == {"$in": ["805"]}
