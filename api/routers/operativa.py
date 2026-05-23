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
from api.services._grupos_scope import scope_cuentas, verificar_account
from api.services.operativa_mep import (
    crear_operativa,
    crear_operativa_venta,
    get_cotizaciones,
    listar_operativas_dia,
    obtener_detalle_operativa,
    serie_mep_minuto,
)

router = APIRouter(prefix="/api/operativa", tags=["operativa"])
logger = logging.getLogger("api.operativa")


class MepIn(BaseModel):
    monto_ars: float = Field(..., gt=0, description="ARS brutos a operar")
    comision_pct: float = Field(0.62, ge=0, le=5, description="Comisión total %")
    rueda: Literal["CI", "24hs"] = "CI"
    account: str | None = None


class MepVentaIn(BaseModel):
    monto_usd: float = Field(..., gt=0, description="USD brutos a vender (vuelven a ARS)")
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


@router.get("/mep/timesales")
def timesales_mep(
    rueda: Literal["CI", "24hs"] = Query("CI"),
    _email: str = Depends(get_user_email),
) -> dict[str, Any]:
    """Serie del MEP por minuto desde el inicio del día (ART). Para el chart de TRADING."""
    try:
        return {"rueda": rueda, "points": serie_mep_minuto(rueda)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("timesales_mep failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/mep")
def crear_mep(
    data: MepIn,
    email: str = Depends(get_user_email),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
) -> dict[str, Any]:
    """Lanza BUY AL30 + SELL AL30D MARKET y persiste el wrapper en
    Operaciones.OperativasMep. Si la BUY rechaza, no se manda la SELL."""
    verificar_account(data.account, scope)
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


@router.post("/mep/venta")
def crear_mep_venta(
    data: MepVentaIn,
    email: str = Depends(get_user_email),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
) -> dict[str, Any]:
    """Inverso de POST /mep: USD → ARS. Lanza BUY AL30D + SELL AL30
    MARKET para recomprar el AL30D que estaba short y vender el AL30
    largo, recibiendo pesos."""
    verificar_account(data.account, scope)
    try:
        return crear_operativa_venta(
            monto_usd=data.monto_usd,
            comision_pct=data.comision_pct,
            rueda=data.rueda,
            account=data.account,
            actor_email=email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("crear_mep_venta failed (email=%s)", email)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/mep/dia")
def listar_dia(
    account: str | None = None,
    _email: str = Depends(get_user_email),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
) -> list[dict]:
    """Operativas MEP del día UTC, con join a OrdenesLive + USD/MEP efectivo."""
    verificar_account(account, scope)
    return listar_operativas_dia(account=account)


@router.get("/mep/{operativa_id}/detalle")
def detalle_operativa(
    operativa_id: str,
    _email: str = Depends(get_user_email),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
) -> dict[str, Any]:
    """Drilldown de una operativa MEP: doc completo + 2 patas con sus
    OrdenesLive y timeline de execution reports + REST snapshots."""
    detalle = obtener_detalle_operativa(operativa_id)
    if detalle is None:
        raise HTTPException(status_code=404, detail=f"operativa {operativa_id!r} no existe")
    verificar_account(detalle.get("account"), scope)
    return detalle
