"""Router /api/derivados/agro — Pase Agro + Estrategias de Cobertura.

Pase Agro (vista PIZARRA, lo que ya estaba):
    GET    /api/derivados/agro                       (admin only por ahora)
    PATCH  /api/derivados/agro/pizarra/{commodity}   (admin only por ahora)

Estrategias de Cobertura (vista ESTRATEGIAS, nueva):
    GET    /api/derivados/agro/opciones/{commodity}      (admin only)
    POST   /api/derivados/agro/estrategia/simular        (admin only)

Todos los endpoints están restringidos a admin mientras la vista está
en beta — cuando la mesa valide, abrimos a sales/trader y dejamos los
PATCH/POST de mutación solo a trader+admin.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services.derivados_agro import (
    COMMODITY_ORDER,
    get_panel_opciones,
    get_pase_agro,
    set_pizarra,
    simular_estrategia,
)
from core.roles import get_user_role

router = APIRouter(prefix="/api/derivados", tags=["DerivadosAgro"])


def _require_admin(email: str) -> None:
    role = get_user_role(email)
    if role != "admin":
        raise HTTPException(
            status_code=403,
            detail=f"role {role!r} no autorizado (solo admin)",
        )


@router.get("/agro")
def pase_agro(email: str = Depends(get_user_email)):
    """Tabla PASE AGRO completa (3 bloques: TRIGO/MAIZ/SOJA) — admin only."""
    _require_admin(email)
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
    _require_admin(email)

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
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail="vencimiento_pizarra debe ser ISO YYYY-MM-DD",
            ) from e

    try:
        new = set_pizarra(
            commodity=commodity,
            vencimiento_pizarra=payload.vencimiento_pizarra,
            us_pizarra=payload.us_pizarra,
            email=email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return new


# ─────────────────────────────────────────────────────────────────────────────
# ESTRATEGIAS DE COBERTURA
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/agro/opciones/{commodity}")
def panel_opciones(commodity: str, email: str = Depends(get_user_email)):
    """Cadena de opciones agro para un commodity, agrupada por vencimiento.

    Lee `Trading.AgroOpcionesSnapshot` (poblado por motor_agro_opciones) +
    embebe `futuro_last` del mismo vencimiento desde `Trading.AgroSnapshot`.
    Admin only.
    """
    _require_admin(email)
    commodity = commodity.upper()
    if commodity not in COMMODITY_ORDER:
        raise HTTPException(
            status_code=400,
            detail=f"commodity inválido. Válidos: {COMMODITY_ORDER}",
        )
    return get_panel_opciones(commodity)


class SimulacionIn(BaseModel):
    commodity:      str
    vencimiento:    str = Field(
        ..., pattern=r"^\d{8}$",
        description="Fecha de vencimiento del contrato (YYYYMMDD)",
    )
    tipo:           Literal["put_sintetico", "long_put"]
    strike:         float = Field(..., gt=0)
    prima_override: float | None = Field(
        default=None, gt=0,
        description="Si se pasa, se usa en lugar del last_price del libro",
    )


@router.post("/agro/estrategia/simular")
def post_simular_estrategia(
    payload: SimulacionIn,
    email: str = Depends(get_user_email),
):
    """Simula put sintético o long put sobre el contrato (commodity, vencimiento, strike).

    Devuelve piso, diferencia máxima, zona expuesta y las dos curvas
    (estrategia_vs_futuro y diferencias) listas para graficar. Admin only.
    """
    _require_admin(email)
    try:
        return simular_estrategia(
            commodity=payload.commodity,
            vencimiento=payload.vencimiento,
            tipo=payload.tipo,
            strike=payload.strike,
            prima_override=payload.prima_override,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
