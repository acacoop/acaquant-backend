"""Golden tests del scope de cuentas por grupo (api/services/_grupos_scope.py).

Cubre el enforcement que protege los endpoints de órdenes/operativa/brackets
(C1, fix 2026-05-23): un usuario scopeado no puede tocar cuentas ajenas.
`scope=None` = admin / sin grupo → sin restricción.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from api.services._grupos_scope import (
    aplicar_scope_cuenta,
    filtrar_cuentas_str,
    filtrar_rows,
    scope_cuenta_match,
    verificar_account,
    verificar_cuenta_str,
)


# ── scope_cuenta_match: igualdad por campo vs regex bracketed ────────────────
def test_scope_match_none_sin_restriccion():
    assert scope_cuenta_match(None) is None
    assert scope_cuenta_match(None, campo="cuenta") is None


def test_scope_match_campo_cuenta_igualdad_indexable():
    # Operaciones: cuenta == id pelado → igualdad $in (NO regex bracketed).
    assert scope_cuenta_match(("100", "201"), campo="cuenta") == {
        "cuenta": {"$in": ["100", "201"]}
    }


def test_scope_match_campo_id_cuenta():
    # NegocioMovimientos: id_cuenta poblado → igualdad $in indexable.
    assert scope_cuenta_match(("100",), campo="id_cuenta") == {"id_cuenta": {"$in": ["100"]}}


def test_scope_match_default_regex_bracketed():
    # Sin campo (FlujosAPI): regex sobre el cuenta bracketed "[id] NOMBRE".
    m = scope_cuenta_match(("100", "201"))
    assert "$regex" in m["cuenta"] and m["cuenta"]["$regex"].startswith("^\\[")


def test_scope_match_vacio_no_matchea_nada():
    assert scope_cuenta_match((), campo="cuenta") == {"cuenta": {"$in": []}}


def test_aplicar_scope_cuenta_agrega_via_and_sin_pisar():
    match = {"moneda": "ARS"}
    aplicar_scope_cuenta(match, ("100",), campo="cuenta")
    assert match["moneda"] == "ARS"  # no pisó el filtro previo
    assert {"cuenta": {"$in": ["100"]}} in match["$and"]

# ── verificar_account: el guard de los endpoints de órdenes ──────────────────

def test_account_sin_scope_pasa_cualquier_cuenta():
    # Admin / sin grupo: scope None → opera cualquier cuenta, incluso None.
    verificar_account("999", None)
    verificar_account(None, None)


def test_account_en_scope_pasa():
    verificar_account("123", ("123", "456"))


def test_account_fuera_de_scope_403():
    with pytest.raises(HTTPException) as exc:
        verificar_account("999", ("123", "456"))
    assert exc.value.status_code == 403


def test_account_none_con_scope_es_400():
    # Un user scopeado DEBE especificar cuenta (no puede caer al default .env).
    with pytest.raises(HTTPException) as exc:
        verificar_account(None, ("123",))
    assert exc.value.status_code == 400


def test_account_scope_vacio_no_matchea_nada():
    # Usuario en un grupo sin cuentas asignadas → no opera ninguna.
    with pytest.raises(HTTPException) as exc:
        verificar_account("123", ())
    assert exc.value.status_code == 403


def test_account_castea_a_str():
    # El scope guarda strings; un account int igual matchea por str().
    verificar_account(123, ("123",))  # type: ignore[arg-type]


# ── verificar_cuenta_str: namespace bracketed "[id] NOMBRE" ──────────────────

def test_cuenta_str_sin_scope_pasa():
    verificar_cuenta_str("[999] CUALQUIERA", None)


def test_cuenta_str_en_scope_pasa():
    verificar_cuenta_str("[123] FOO SA", ("123",))


def test_cuenta_str_fuera_de_scope_403():
    with pytest.raises(HTTPException) as exc:
        verificar_cuenta_str("[999] BAR SA", ("123",))
    assert exc.value.status_code == 403


def test_cuenta_str_sin_bracket_403():
    with pytest.raises(HTTPException):
        verificar_cuenta_str("SIN BRACKET", ("123",))


# ── filtros de listas ────────────────────────────────────────────────────────

def test_filtrar_rows_sin_scope_no_cambia():
    rows = [{"id_cuenta": "1"}, {"id_cuenta": "2"}]
    assert filtrar_rows(rows, None) == rows


def test_filtrar_rows_aplica_scope():
    rows = [{"id_cuenta": "1"}, {"id_cuenta": "2"}, {"id_cuenta": "3"}]
    out = filtrar_rows(rows, ("1", "3"))
    assert [r["id_cuenta"] for r in out] == ["1", "3"]


def test_filtrar_cuentas_str_por_id_bracketed():
    cuentas = ["[1] A", "[2] B", "[3] C"]
    assert filtrar_cuentas_str(cuentas, ("2",)) == ["[2] B"]
