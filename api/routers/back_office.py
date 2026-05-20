"""Router /api/back-office — sección Back Office.

Por ahora solo expone Títulos / Mercado (qué títulos hay que enviar y
recibir hoy con el mercado, derivado de `CashFlow.NegocioMovimientos`).
Más sub-vistas se irán sumando acá conforme se vayan definiendo.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.auth import get_user_email
from api.services.back_office_titulos import get_titulos_mercado

router = APIRouter(prefix="/api/back-office", tags=["BackOffice"])


@router.get("/titulos-mercado")
def titulos_mercado(
    fecha: str | None = Query(None, description="ISO YYYY-MM-DD; default = hoy"),
    _email: str = Depends(get_user_email),
):
    """Títulos a enviar/recibir al mercado para `fecha` (default hoy).

    Settlement = (ops de `fecha` con plazo CI/Inm) + (ops del día hábil
    anterior con plazo 24hs). Si `fecha` no es día hábil devuelve
    estructura vacía con `mercado_cerrado: true`.
    """
    return get_titulos_mercado(fecha=fecha)
