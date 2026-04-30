"""Router /api/derivados/agro — Pase Agro (Trigo/Maíz/Soja Rosario).

GET    /api/derivados/agro                       (admin only por ahora)
PATCH  /api/derivados/agro/pizarra/{commodity}   (admin only por ahora)

Tanto el GET como el PATCH están restringidos a admin mientras la vista
está en beta — el resto de /derivados (Opciones) sigue público para los
roles con módulo derivados. Cuando la mesa valide la tabla, abrimos el
GET a sales/trader y dejamos el PATCH solo a trader+admin.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services.derivados_agro import COMMODITY_ORDER, get_pase_agro, set_pizarra
from core.roles import get_user_role

router = APIRouter(prefix="/api/derivados", tags=["DerivadosAgro"])


@router.get("/agro")
def pase_agro(email: str = Depends(get_user_email)):
    """Tabla PASE AGRO completa (3 bloques: TRIGO/MAIZ/SOJA) — admin only."""
    role = get_user_role(email)
    if role != "admin":
        raise HTTPException(
            status_code=403,
            detail=f"role {role!r} no autorizado para ver pase agro (solo admin)",
        )
    return get_pase_agro()


class PizarraIn(BaseModel):
    vencimiento_pizarra: str | None = Field(
        default=None, description="Fecha ISO YYYY-MM-DD; null = no tocar"
    )
    us_pizarra: float | None = Field(
        default=None, gt=0, description="Precio pizarra en US$; null = no tocar"
    )


@router.patch("/agro/pizarra/{commodity}")
def patch_pizarra(
    commodity: str,
    payload: PizarraIn,
    email: str = Depends(get_user_email),
):
    """Upsert de la fila PIZARRA — admin only (beta)."""
    role = get_user_role(email)
    if role != "admin":
        raise HTTPException(
            status_code=403,
            detail=f"role {role!r} no autorizado para editar pizarra agro",
        )

    commodity = commodity.upper()
    if commodity not in COMMODITY_ORDER:
        raise HTTPException(
            status_code=400,
            detail=f"commodity inválido. Válidos: {COMMODITY_ORDER}",
        )

    # Sanity-check del formato de fecha si vino
    if payload.vencimiento_pizarra:
        try:
            datetime.strptime(payload.vencimiento_pizarra, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="vencimiento_pizarra debe ser ISO YYYY-MM-DD",
            )

    try:
        new = set_pizarra(
            commodity=commodity,
            vencimiento_pizarra=payload.vencimiento_pizarra,
            us_pizarra=payload.us_pizarra,
            email=email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return new
