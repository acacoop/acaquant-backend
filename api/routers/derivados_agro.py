"""Router /api/derivados/agro — Pase Agro (Trigo/Maíz/Soja Rosario).

GET    /api/derivados/agro                       (público — todos los roles ven derivados)
PATCH  /api/derivados/agro/pizarra/{commodity}   (gate inline: trader+admin)

El PATCH usa un check inline contra core.roles.get_user_role en lugar del
módulo "operaciones" o "operar" porque la pizarra agro no encaja
semánticamente en ninguno de esos: "operar" incluye a sales (puede operar
DOLAR MEP) y "operaciones" es la mesa de flujos. Acá queremos exactamente
el subset trader+admin, sin acoplarnos a otra matriz.
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
def pase_agro():
    """Tabla PASE AGRO completa (3 bloques: TRIGO/MAIZ/SOJA)."""
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
    """Upsert de la fila PIZARRA — solo trader+admin."""
    role = get_user_role(email)
    if role not in ("trader", "admin"):
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
