"""Tests de integración de la vista COMERCIAL — contra Atlas.

Corre con:  pytest -m integration   (excluido del run unit por defecto).
Skipea si Mongo no está disponible (entornos sin MONGO_URI / CI).

Valida invariantes de consistencia y que las queries calientes usen índice
(IXSCAN), no escaneo total (COLLSCAN).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def db():
    try:
        from core.mongo import get_mongo_client_read
        client = get_mongo_client_read()
        client.admin.command("ping")
    except Exception as e:
        pytest.skip(f"Mongo no disponible: {e}")
    return client


@pytest.fixture(scope="module")
def operador():
    from api.services.comercial import listar_operadores_comercial
    ops = listar_operadores_comercial()
    if not ops:
        pytest.skip("sin operadores en Clientes.Comitentes")
    return ops[0]["operador_email"]


def test_invariantes_operador_comercial(db, operador):
    from api.services.comercial import operador_comercial
    data = operador_comercial(operador=operador)
    r, cli = data["resumen"], data["clientes"]

    assert r["n_clientes"] == len(cli), "n_clientes != filas de clientes"
    assert abs(sum(c["aum"] for c in cli) - r["aum_gestionado"]) < 1.0, "Σ AuM clientes != total"
    assert abs(sum(c["volumen_ytd"] for c in cli) - r["volumen_ytd"]) < 1.0, "Σ vol YTD != total"
    if cli:
        assert "nivel_1" in cli[0]["ficha"], "ficha sin campos de segmentación"


def test_no_quedan_docs_sin_id_cuenta(db):
    """Tras el backfill, todos los movimientos deben tener id_cuenta."""
    nm = db["CashFlow"]["NegocioMovimientos"]
    falta = nm.count_documents({"id_cuenta": {"$exists": False}})
    assert falta == 0, f"{falta} docs sin id_cuenta — correr scripts.backfill_id_cuenta_negocio"


def test_volumen_usa_indice(db):
    """La query de volumen por cuenta debe resolver por índice (no COLLSCAN)."""
    from api.services.comercial import _CATS_VOLUMEN
    nm = db["CashFlow"]["NegocioMovimientos"]
    plan = nm.find(
        {"id_cuenta": {"$in": ["805"]}, "categoria": {"$in": list(_CATS_VOLUMEN)}, "moneda": "ARS"}
    ).explain()
    assert "IXSCAN" in str(plan.get("queryPlanner", {})), "volumen no usa índice (COLLSCAN)"


def test_operaciones_cliente_usa_indice(db):
    from api.services.comercial import _CATS_OPERACIONES
    nm = db["CashFlow"]["NegocioMovimientos"]
    plan = nm.find(
        {"id_cuenta": "805", "categoria": {"$in": list(_CATS_OPERACIONES)}}
    ).sort([("fecha", -1)]).explain()
    assert "IXSCAN" in str(plan.get("queryPlanner", {})), "operaciones no usa índice (COLLSCAN)"
