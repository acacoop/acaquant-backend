"""Router /api/derivados/agro — Pase Agro + Estrategias + Cámara + Mejoras Dispo.

    GET    /api/derivados/agro                       (todos los roles)
    PATCH  /api/derivados/agro/pizarra/{commodity}   (todos los roles)
    GET    /api/derivados/agro/opciones/{commodity}  (todos los roles)
    POST   /api/derivados/agro/estrategia/simular    (todos los roles)
    GET    /api/derivados/agro/camara                (todos los roles)
    PATCH  /api/derivados/agro/camara/{cereal}       (todos los roles)
    GET    /api/derivados/agro/mejoras-dispo         (todos los roles)

Acceso abierto a admin / trader / sales — el módulo `agro` está en la
RoleMatrix para los 3 roles; `get_user_email` se mantiene para audit.

La URL del router se mantiene en `/api/derivados/agro/*` por compatibilidad
con la frontend deployada — la separación de Agro como módulo top-level es
puramente lógica (en `core/roles.py::MODULES`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_user_email, require_module
from api.services.camara_cereales import (
    CEREALES,
    get_camara_cereales,
    set_camara_cereal,
)
from api.services.derivados_agro import (
    COMMODITY_ORDER,
    get_panel_opciones,
    get_pase_agro,
    set_pizarra,
    simular_estrategia,
)
from api.services.mejoras_dispo import get_mejoras_dispo

router = APIRouter(prefix="/api/derivados", tags=["DerivadosAgro"])


@router.get("/agro")
def pase_agro(_email: str = Depends(get_user_email)):
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
    _mod: None = Depends(require_module("agro")),
):
    """Upsert de la fila PIZARRA — email queda en el audit."""
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
def panel_opciones(commodity: str, _email: str = Depends(get_user_email)):
    """Cadena de opciones agro para un commodity, agrupada por vencimiento.

    Lee `Trading.AgroOpcionesSnapshot` (poblado por motor_agro_opciones) +
    embebe `futuro_last` del mismo vencimiento desde `Trading.AgroSnapshot`.
    """
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
    _email: str = Depends(get_user_email),
):
    """Simula put sintético o long put sobre el contrato (commodity, vencimiento, strike).

    Devuelve piso, diferencia máxima, zona expuesta y las dos curvas
    (estrategia_vs_futuro y diferencias) listas para graficar.
    """
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


# ─────────────────────────────────────────────────────────────────────────────
# CÁMARA ARBITRAL DE CEREALES (input manual, 5 cereales)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/agro/camara")
def camara_cereales(_email: str = Depends(get_user_email)):
    """Lista los 5 cereales de la Cámara — siempre los 5, vacíos si no cargados."""
    return get_camara_cereales()


class CamaraCerealIn(BaseModel):
    precio_ars: float | None = Field(
        default=None, gt=0,
        description="Precio en ARS (Pizarra Rosario); null = no tocar",
    )
    precio_usd: float | None = Field(
        default=None, gt=0,
        description="Precio en USD (oficial Cámara); null = no tocar",
    )


@router.patch("/agro/camara/{cereal}")
def patch_camara_cereal(
    cereal: str,
    payload: CamaraCerealIn,
    email: str = Depends(get_user_email),
    _mod: None = Depends(require_module("agro")),
):
    """Upsert de un cereal (precio_ars / precio_usd). Audit en CamaraCerealesAudit."""
    cereal = cereal.upper()
    if cereal not in CEREALES:
        raise HTTPException(
            status_code=400,
            detail=f"cereal inválido. Válidos: {CEREALES}",
        )
    try:
        return set_camara_cereal(
            cereal=cereal,
            precio_ars=payload.precio_ars,
            precio_usd=payload.precio_usd,
            email=email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ─────────────────────────────────────────────────────────────────────────────
# MEJORAS PRECIO DISPONIBLE (Soja / Maíz / Trigo + LECAP)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/agro/mejoras-dispo")
def mejoras_dispo(_email: str = Depends(get_user_email)):
    """3 bloques (Soja/Maíz/Trigo). Combina Cámara.precio_ars + TNA LECAPs +
    futuros DLR para mostrarle al productor cuánto cobra si se queda en
    LECAP + se cubre con futuro."""
    return get_mejoras_dispo()
