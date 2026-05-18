"""Endpoints de datos del partner_api — SOLO lectura de Partner.PortfolioExport.

Todos requieren un Bearer token válido (`Depends(usuario_actual)`).
La colección sólo contiene las cuentas de `config.PARTNER_EXPORT_CUENTAS`,
así que no hay forma de pedir una cuenta que no esté habilitada.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from partner_api.auth import usuario_actual
from partner_api.db import get_db
from partner_api.ratelimit import limiter

router = APIRouter(prefix="/v1", tags=["portfolio"])

# Campos internos que NO se devuelven al proveedor.
_HIDE = {"_id": 0, "exported_at": 0}


@router.get("/fechas")
@limiter.limit("60/hour")
def fechas(request: Request, _user: str = Depends(usuario_actual)) -> dict:
    """Fechas disponibles en el export, de la más reciente a la más vieja."""
    col = get_db()["PortfolioExport"]
    valores = sorted((f for f in col.distinct("fecha") if f), reverse=True)
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
    col = get_db()["PortfolioExport"]

    if not fecha:
        last = col.find_one(
            {}, {"_id": 0, "fecha": 1}, sort=[("fecha", -1)],
        )
        if not last:
            return {"fecha": None, "posiciones": [], "n": 0}
        fecha = last["fecha"]

    filtro: dict = {"fecha": fecha}
    if id_cuenta:
        filtro["id_cuenta"] = str(id_cuenta).strip()

    docs = list(col.find(filtro, _HIDE).sort([("id_cuenta", 1), ("unidad", 1)]))
    return {"fecha": fecha, "posiciones": docs, "n": len(docs)}
