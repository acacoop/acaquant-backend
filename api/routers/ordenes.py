"""Router /api/ordenes — envío/cancel/listado de órdenes contra ROFEX (LIVE).

El gate RBAC se aplica en `api/main.py` vía `_OPERACIONES` (admin+trader).
Acá solo agregamos `Depends(get_user_email)` por endpoint para registrar
el actor en el audit log de Mongo.

Endpoints:
  POST   /api/ordenes              → envía (LIMIT|MARKET, BUY|SELL, DAY|IOC|FOK|GTC)
  DELETE /api/ordenes/{cl_ord_id}  → cancela
  GET    /api/ordenes/dia          → órdenes del día UTC para la cuenta default
  GET    /api/ordenes/{cl_ord_id}  → estado actual (lo mantiene el motor)

Diseño: los routers son thin wrappers; toda la lógica está en
`api/services/ordenes.py`. La sesión pyRofex es lazy — la primera llamada
a send/cancel inicializa la sesión REST en el proceso uvicorn.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services.ordenes import (
    cancel_order,
    get_order_status,
    list_orders_dia,
    send_order,
)

router = APIRouter(prefix="/api/ordenes", tags=["ordenes"])
logger = logging.getLogger("api.ordenes")


class OrdenIn(BaseModel):
    ticker: str = Field(..., description="Símbolo full, ej 'MERV - XMEV - AL30 - 24hs'")
    side: Literal["BUY", "SELL"]
    size: int = Field(..., gt=0, description="Nominales, > 0")
    order_type: Literal["LIMIT", "MARKET"] = "LIMIT"
    price: float | None = Field(None, description="Requerido si order_type=LIMIT")
    tif: Literal["DAY", "IOC", "FOK", "GTC"] = "DAY"
    account: str | None = Field(None, description="Si None, usa la del .env")


@router.post("", status_code=201)
def enviar(
    data: OrdenIn,
    email: str = Depends(get_user_email),
) -> dict[str, Any]:
    """Envía la orden via REST y persiste request + respuesta en Mongo.

    Devuelve `{ok, cl_ord_id, status, error?, broker_response}`. Si el
    broker rechaza, `ok=False` y status indica si fue local (validación)
    o broker (rechazo del broker).
    """
    try:
        return send_order(
            ticker=data.ticker,
            side=data.side,
            size=data.size,
            order_type=data.order_type,
            price=data.price,
            tif=data.tif,
            account=data.account,
            actor_email=email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("send_order failed (email=%s ticker=%s)", email, data.ticker)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{cl_ord_id}")
def cancelar(
    cl_ord_id: str,
    email: str = Depends(get_user_email),
) -> dict[str, Any]:
    """Pide cancelación al broker. El estado real llega vía order_report
    al motor de órdenes — acá solo registramos el intento."""
    try:
        return cancel_order(cl_ord_id, actor_email=email)
    except Exception as e:
        logger.exception("cancel_order failed (email=%s cl_ord_id=%s)", email, cl_ord_id)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/dia")
def listar_dia(
    _email: str = Depends(get_user_email),
) -> list[dict]:
    """Órdenes del día UTC actual para la cuenta default."""
    return list_orders_dia()


@router.get("/{cl_ord_id}")
def status(
    cl_ord_id: str,
    _email: str = Depends(get_user_email),
) -> dict[str, Any]:
    """Estado actual de una orden (lo mantiene el motor con cada ER)."""
    doc = get_order_status(cl_ord_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="orden no encontrada")
    return doc
