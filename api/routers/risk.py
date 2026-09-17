"""Router /api/risk — datos de cuenta del broker (saldos, posiciones, márgenes).

Datos sensibles. Gate _OPERACIONES (admin+trader) en api/main.py.

Endpoints:
  GET /api/risk/account/saldo?rueda=CI[&account=X]    → ARS + USD MEP para la rueda
  GET /api/risk/account/report[?account=X]            → report crudo del broker
  GET /api/risk/account/positions[?account=X]         → posiciones (símbolo, size, px)
  GET /api/risk/account/detailed[?account=X]          → posiciones detalladas por tipo
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import get_user_email
from api.deps import scope_cuentas, verificar_account
from api.services.risk import (
    account_detailed_position,
    account_positions,
    account_report,
    listado_cuentas,
    saldo_para_rueda,
)

router = APIRouter(prefix="/api/risk", tags=["risk"])
logger = logging.getLogger("api.risk")


@router.get("/account/saldo")
def saldo(
    rueda: Literal["CI", "24hs"] = Query("CI"),
    account: str | None = None,
    _email: str = Depends(get_user_email),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
) -> dict[str, Any]:
    """Saldo ARS + USD MEP (USD D) disponible para operar en la rueda elegida.

    Lo consume la UI DOLAR MEP para mostrar saldo en vivo. Cache 3s en el
    service — pegale a este endpoint todo lo que quieras, no satura al broker.
    """
    verificar_account(account, scope)
    try:
        return saldo_para_rueda(rueda=rueda, account=account)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("saldo failed (rueda=%s account=%s)", rueda, account)
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/account/report")
def report(
    account: str | None = None,
    _email: str = Depends(get_user_email),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
) -> dict[str, Any]:
    """Reporte crudo de pyRofex. Útil para vistas tipo cartera con todos los
    bloques (collateral, margin, portfolio, monedas múltiples)."""
    verificar_account(account, scope)
    try:
        return account_report(account=account)
    except Exception as e:
        logger.exception("report failed (account=%s)", account)
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/account/positions")
def positions(
    account: str | None = None,
    _email: str = Depends(get_user_email),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
) -> dict[str, Any]:
    """Posiciones agregadas: ticker, buySize, buyPrice, sellSize, sellPrice."""
    verificar_account(account, scope)
    try:
        return account_positions(account=account)
    except Exception as e:
        logger.exception("positions failed (account=%s)", account)
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/account/detailed")
def detailed(
    account: str | None = None,
    _email: str = Depends(get_user_email),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
) -> dict[str, Any]:
    """Posiciones detalladas por tipo de instrumento (BOND / NEG_OBLIG / etc.)
    con valuación a market."""
    verificar_account(account, scope)
    try:
        return account_detailed_position(account=account)
    except Exception as e:
        logger.exception("detailed failed (account=%s)", account)
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/account/listado")
def listado(
    solo_activas: bool = False,
    _email: str = Depends(get_user_email),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
) -> list[dict[str, Any]]:
    """Listado de cuentas para operar — espejo de las comitentes de SQL
    `clientes.cuentas` (fuente única, ver `risk.listado_cuentas`).

    NO pega al broker. Lo consume el dropdown de cuentas del frontend
    en /operar. `solo_activas=true` filtra las que tengan saldo o
    posiciones; default `false` muestra todas las autorizadas.

    Filtrado por scope de grupos: un user scopeado solo ve sus cuentas en el
    dropdown (admin / sin-grupo ven todas).
    """
    rows = listado_cuentas(solo_activas=solo_activas)
    if scope is not None:
        permitidas = set(scope)
        rows = [r for r in rows if str(r.get("account_id")) in permitidas]
    return rows
