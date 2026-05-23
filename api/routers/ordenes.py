"""Router /api/ordenes — envío/cancel/listado de órdenes contra ROFEX (LIVE).

El gate RBAC de MÓDULO se aplica en `api/main.py` vía `_OPERAR` (admin/
trader/sales tienen `operar`). Además, cada endpoint que toca una cuenta
aplica SCOPE de grupos (`verificar_account`): un user scopeado solo opera/
cancela/ve órdenes de sus cuentas; admin / sin-grupo (`scope=None`) opera
todo. El scope es no-op hasta que el admin cree grupos en /manager.
`Depends(get_user_email)` registra el actor en el audit log de Mongo.

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

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services._grupos_scope import scope_cuentas, verificar_account
from api.services.ordenes import (
    cancel_order,
    get_fci_quote,
    get_order_status,
    list_orders_dia,
    search_fci,
    search_symbols,
    send_fci_order,
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
    client_order_id: str | None = Field(
        None,
        description="Clave de idempotencia opcional — un reenvío con la misma "
        "clave no genera otra orden. Si se omite, comportamiento de siempre.",
    )


@router.post("", status_code=201)
def enviar(
    data: OrdenIn,
    email: str = Depends(get_user_email),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
) -> dict[str, Any]:
    """Envía la orden via REST y persiste request + respuesta en Mongo.

    Devuelve `{ok, cl_ord_id, status, error?, broker_response}`. Si el
    broker rechaza, `ok=False` y status indica si fue local (validación)
    o broker (rechazo del broker).
    """
    verificar_account(data.account, scope)
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
            client_order_id=data.client_order_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("send_order failed (email=%s ticker=%s)", email, data.ticker)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{cl_ord_id}")
def cancelar(
    cl_ord_id: str,
    proprietary: str | None = Query(
        None,
        description="Opcional — para cancelar órdenes external (vinieron solo del broker, no de nuestra app)",
    ),
    email: str = Depends(get_user_email),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
) -> dict[str, Any]:
    """Pide cancelación al broker. El estado real llega vía order_report
    al motor de órdenes — acá solo registramos el intento."""
    if scope is not None:
        # Resolver la cuenta de la orden y verificar que sea del scope antes
        # de cancelar (no alcanza el cl_ord_id: un user scopeado no puede
        # cancelar órdenes de cuentas ajenas).
        doc = get_order_status(cl_ord_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="orden no encontrada")
        verificar_account(doc.get("account"), scope)
    try:
        return cancel_order(cl_ord_id, actor_email=email, proprietary=proprietary)
    except Exception as e:
        logger.exception("cancel_order failed (email=%s cl_ord_id=%s)", email, cl_ord_id)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/dia")
def listar_dia(
    account: str | None = Query(
        None,
        description="ID de cuenta a filtrar; si se omite usa la cuenta default del .env",
    ),
    _email: str = Depends(get_user_email),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
) -> list[dict]:
    """Órdenes del día UTC actual. Filtrable por cuenta — el Dashboard
    de Operar manda la cuenta seleccionada en el toolbar, así no muestra
    las del default cuando estás operando en otra."""
    verificar_account(account, scope)
    return list_orders_dia(account=account)


@router.get("/symbols")
def buscar_symbols(
    q: str = Query(..., min_length=2, description="Substring para matchear ticker o underlying (case-insensitive)"),
    limit: int = Query(20, ge=1, le=50),
    _email: str = Depends(get_user_email),
) -> list[dict]:
    """Autocomplete del campo TICKER en /operar → PRUEBA. Devuelve top
    `limit` instruments que matcheen `q` en ticker o underlying. Excluye
    FCI (no operables vía pyRofex)."""
    return search_symbols(q, limit=limit)


# ─── FCI: suscripción / rescate (flujo separado de assets) ───────────────────

class FciOrdenIn(BaseModel):
    ticker: str = Field(..., description="Ticker del FCI (cficode CIO…)")
    side: Literal["BUY", "SELL"] = Field(..., description="BUY=suscripción, SELL=rescate")
    amount: float = Field(..., gt=0, description="Importe o cuotapartes según amount_mode")
    amount_mode: Literal["cuotapartes", "importe"] = "cuotapartes"
    account: str | None = Field(None, description="Si None, usa la del .env")
    client_order_id: str | None = Field(
        None, description="Clave de idempotencia opcional (anti doble suscripción/rescate).",
    )


@router.get("/fci/search")
def buscar_fci(
    q: str = Query(..., min_length=2, description="Substring de ticker o underlying del FCI"),
    limit: int = Query(30, ge=1, le=50),
    _email: str = Depends(get_user_email),
) -> list[dict]:
    """Buscador de FCI (solo cficode CIO…) — universo opuesto a /symbols."""
    return search_fci(q, limit=limit)


@router.get("/fci/quote")
def cotizar_fci(
    ticker: str = Query(..., description="Ticker del FCI"),
    _email: str = Depends(get_user_email),
) -> dict[str, Any]:
    """Cuota + metadata operable de un FCI (conversión importe↔cuotapartes)."""
    try:
        return get_fci_quote(ticker)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/fci", status_code=201)
def enviar_fci(
    data: FciOrdenIn,
    email: str = Depends(get_user_email),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
) -> dict[str, Any]:
    """Suscripción (BUY) / rescate (SELL) de un FCI. Orden LIMIT @ cuota del
    día, cantidad en cuotapartes (convertida desde importe si corresponde)."""
    verificar_account(data.account, scope)
    try:
        return send_fci_order(
            ticker=data.ticker,
            side=data.side,
            amount=data.amount,
            amount_mode=data.amount_mode,
            account=data.account,
            actor_email=email,
            client_order_id=data.client_order_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("send_fci_order failed (email=%s ticker=%s)", email, data.ticker)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{cl_ord_id}")
def status(
    cl_ord_id: str,
    _email: str = Depends(get_user_email),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
) -> dict[str, Any]:
    """Estado actual de una orden (lo mantiene el motor con cada ER)."""
    doc = get_order_status(cl_ord_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="orden no encontrada")
    verificar_account(doc.get("account"), scope)
    return doc
