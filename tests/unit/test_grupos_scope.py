"""Golden tests del scope de cuentas por grupo (api/deps.py).

Cubre el enforcement que protege los endpoints de órdenes/operativa/brackets
(C1, fix 2026-05-23): un usuario scopeado no puede tocar cuentas ajenas.
`scope=None` = admin / sin grupo → sin restricción.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from api.deps import verificar_account, verificar_cuenta_str

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
