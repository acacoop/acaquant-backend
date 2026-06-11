"""Tests de api/services/operaciones_view.py — la lógica pura que antes vivía
presa dentro del router (AUDITORIA A2). Fija las reglas de dominio NO
inferibles: separación es_cierre (volumen vs arancel), desdoble FCI bilateral
y conversión por MEP histórico."""
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


# ── ops_match: VOLUMEN excluye el cierre y desdobla FCI bilateral ────────────

def test_ops_match_excluye_cierre():
    m = ov.ops_match("ARS", None)
    assert m["es_cierre"] is False
    assert m["moneda"] == "ARS"


def test_ops_match_nor_fci_bilateral():
    # No doblar el volumen: fuera la liquidación de Suscripción y la solicitud de Rescate.
    m = ov.ops_match("ARS", None)
    assert {"operacion": "Suscripción", "etapa": "liquidacion"} in m["$nor"]
    assert {"operacion": "Rescate", "etapa": "solicitud"} in m["$nor"]


def test_ops_match_todos_no_filtra():
    m = ov.ops_match("USD", "todos", segmento="todos")
    assert "mercado" not in m  # 'todos' = sin filtro
    assert "segmento" not in m


def test_ops_match_filtros_opcionales():
    m = ov.ops_match("ARS", "BYMA", operacion="Compra", denominacion="AL30", cuenta="123")
    assert m["mercado"] == "BYMA"
    assert m["operacion"] == "Compra"
    assert m["denominacion"] == "AL30"
    assert m["cuenta"] == "123"


# ── arancel_match: ARANCEL incluye los cierres con fee (caución) ─────────────

def test_arancel_match_incluye_cierres_con_arancel():
    m = ov.arancel_match("ARS")
    # ya no filtra es_cierre directo; el $or admite cierres con arancel != 0
    assert "es_cierre" not in m
    assert {"es_cierre": False} in m["$or"]
    assert {"es_cierre": True, "arancel": {"$ne": 0}} in m["$or"]


def test_arancel_match_conserva_nor_y_moneda():
    m = ov.arancel_match("USD", segmento="Acciones")
    assert m["moneda"] == "USD"
    assert m["segmento"] == "Acciones"
    assert "$nor" in m  # mismo desdoble FCI que ops_match


# ── importe_convertido: conversión por MEP histórico del boleto ──────────────

def test_importe_convertido_a_usd():
    conv = ov.importe_convertido("USD")
    # USD nativo → |importe| directo; otra moneda → /mep (con guard mep>0)
    assert conv["$cond"][0] == {"$eq": ["$moneda", "USD"]}
    rama_no_usd = conv["$cond"][2]
    assert "$divide" in str(rama_no_usd)


def test_importe_convertido_a_ars():
    conv = ov.importe_convertido("ARS")
    assert conv["$cond"][0] == {"$eq": ["$moneda", "ARS"]}
    assert "$multiply" in str(conv["$cond"][2])


def test_valor_si_categoria():
    v = ov.valor_si_categoria("compra", {"x": 1})
    assert v == {"$cond": [{"$eq": ["$categoria", "compra"]}, {"x": 1}, 0]}
