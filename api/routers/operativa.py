"""Router /api/operativa — wrappers operativos sobre /api/ordenes.

Por ahora solo MEP (BUY AL30 + SELL AL30D). Pensado para crecer con CCL,
canjes, etc. Cada wrapper queda en su sub-path para que el frontend pueda
versionar tabs sin pisarse.

RBAC: heredado del módulo `operaciones` vía `_OPERACIONES` en api/main.py.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services.operativa_mep import (
    crear_operativa,
    get_cotizaciones,
    listar_operativas_dia,
)

router = APIRouter(prefix="/api/operativa", tags=["operativa"])
logger = logging.getLogger("api.operativa")


class MepIn(BaseModel):
    monto_ars: float = Field(..., gt=0, description="ARS brutos a operar")
    comision_pct: float = Field(0.62, ge=0, le=5, description="Comisión total %")
    rueda: Literal["CI", "24hs"] = "CI"
    account: str | None = None


@router.get("/mep/cotizacion")
def cotizacion_mep(
    rueda: Literal["CI", "24hs"] = Query("CI"),
    _email: str = Depends(get_user_email),
) -> dict[str, Any]:
    """Last price live de AL30 / AL30D + MEP implícito (= AL30 / AL30D)."""
    try:
        return get_cotizaciones(rueda)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/mep")
def crear_mep(
    data: MepIn,
    email: str = Depends(get_user_email),
) -> dict[str, Any]:
    """Lanza BUY AL30 + SELL AL30D MARKET y persiste el wrapper en
    Operaciones.OperativasMep. Si la BUY rechaza, no se manda la SELL."""
    try:
        return crear_operativa(
            monto_ars=data.monto_ars,
            comision_pct=data.comision_pct,
            rueda=data.rueda,
            account=data.account,
            actor_email=email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("crear_mep failed (email=%s)", email)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/mep/dia")
def listar_dia(
    account: str | None = None,
    _email: str = Depends(get_user_email),
) -> list[dict]:
    """Operativas MEP del día UTC, con join a OrdenesLive + USD/MEP efectivo."""
    return listar_operativas_dia(account=account)
