"""Endpoints de datos del partner_api — SOLO lectura del portfolio del proveedor.

Todos requieren un Bearer token válido (`Depends(usuario_actual)`).
El dataset sólo contiene las cuentas de `config.PARTNER_EXPORT_CUENTAS`, así que
no hay forma de pedir una cuenta que no esté habilitada.

Fuente de datos: `partner_api.store` (SQL-native (partner.cartera)).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from partner_api import store
from partner_api.auth import usuario_actual
from partner_api.ratelimit import limiter

router = APIRouter(prefix="/v1", tags=["portfolio"])


@router.get("/fechas")
@limiter.limit("60/hour")
def fechas(request: Request, _user: str = Depends(usuario_actual)) -> dict:
    """Fechas disponibles en el export, de la más reciente a la más vieja."""
    valores = store.distinct_fechas()
    return {"fechas": valores, "n": len(valores)}


@router.get("/portfolio")
@limiter.limit("60/hour")
def portfolio(
    request: Request,
    fecha: str | None = Query(
        None, description="YYYY-MM-DD. Si se omite, usa la fecha más reciente."
    ),
    id_cuenta: str | None = Query(
        None, description="Filtra por una cuenta puntual (ej. '101')."
    ),
    _user: str = Depends(usuario_actual),
) -> dict:
    """Posiciones de portfolio de las cuentas habilitadas.

    Una fila por (cuenta, instrumento) con cantidad, precio y valuación.
    """
    if not fecha:
        fecha = store.latest_fecha()
        if not fecha:
            return {"fecha": None, "posiciones": [], "n": 0}

    posiciones = store.portfolio(fecha, id_cuenta)
    return {"fecha": fecha, "posiciones": posiciones, "n": len(posiciones)}
