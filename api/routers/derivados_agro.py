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

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email, require_module, require_no_invitado
from api.services import agro_sql as _agro_sql
from api.services.camara_cereales import (
    CEREALES,
    get_dolares_referencia,
    get_tasas_cobertura,
    set_camara_cereal,
    set_dolares_referencia,
    set_tasas_cobertura,
)
from api.services.derivados_agro import (
    COMMODITY_ORDER,
    set_pizarra,
)

router = APIRouter(prefix="/api/derivados", tags=["DerivadosAgro"])


def _agro_svc(_engine: str | None = None):
    """Lecturas AGRO (pase, opciones, simulador, cámara, mejoras-dispo): SQL-only
    (`agro_sql` → mercado.agro_*). SQL-native (decomiso Mongo). Las escrituras (PATCH)
    viven en los services originales y ya escriben SQL (write_native). El parámetro
    `_engine` queda por compat con `?_engine` — ya no selecciona."""
    return _agro_sql


@router.get("/agro")
def pase_agro(
    _email: str = Depends(get_user_email),
    _engine: str | None = Query(None, include_in_schema=False),
):
    """Tabla PASE AGRO completa (3 bloques: TRIGO/MAIZ/SOJA)."""
    return _agro_svc(_engine).get_pase_agro()


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
    _noguest: None = Depends(require_no_invitado),   # escritura: bloquea invitado www (REGLA #8)
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
def panel_opciones(
    commodity: str,
    _email: str = Depends(get_user_email),
    _engine: str | None = Query(None, include_in_schema=False),
):
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
    return _agro_svc(_engine).get_panel_opciones(commodity)


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
    _engine: str | None = Query(None, include_in_schema=False),
):
    """Simula put sintético o long put sobre el contrato (commodity, vencimiento, strike).

    Devuelve piso, diferencia máxima, zona expuesta y las dos curvas
    (estrategia_vs_futuro y diferencias) listas para graficar.
    """
    try:
        return _agro_svc(_engine).simular_estrategia(
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
def camara_cereales(
    _email: str = Depends(get_user_email),
    _engine: str | None = Query(None, include_in_schema=False),
):
    """Lista los 5 cereales de la Cámara — siempre los 5, vacíos si no cargados."""
    return _agro_svc(_engine).get_camara_cereales()


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
    _noguest: None = Depends(require_no_invitado),   # escritura: bloquea invitado www (REGLA #8)
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
# TASAS DE COBERTURA (ON / Pagaré) — inputs manuales globales de la tab DATOS
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/agro/tasas-cobertura")
def tasas_cobertura(
    _email: str = Depends(get_user_email),
):
    """Tasas manuales ON / Pagaré (globales) que alimentan el Pase con Cobertura."""
    return get_tasas_cobertura()


class TasasCoberturaIn(BaseModel):
    tasa_on: float | None = Field(
        default=None, gt=0, description="Tasa ON en %; null = no tocar",
    )
    tasa_pagare: float | None = Field(
        default=None, gt=0, description="Tasa Pagaré en %; null = no tocar",
    )


@router.patch("/agro/tasas-cobertura")
def patch_tasas_cobertura(
    payload: TasasCoberturaIn,
    email: str = Depends(get_user_email),
    _mod: None = Depends(require_module("agro")),
    _noguest: None = Depends(require_no_invitado),   # escritura: bloquea invitado www (REGLA #8)
):
    """Upsert de las tasas ON / Pagaré (globales)."""
    try:
        return set_tasas_cobertura(
            tasa_on=payload.tasa_on,
            tasa_pagare=payload.tasa_pagare,
            email=email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ─────────────────────────────────────────────────────────────────────────────
# DÓLARES DE REFERENCIA (Banco Nación / Matba Rofex) — inputs manuales globales
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/agro/dolares-referencia")
def dolares_referencia(
    _email: str = Depends(get_user_email),
):
    """Dólares manuales Banco Nación / Matba Rofex (globales) que alimentarán el
    Pase con Cobertura."""
    return get_dolares_referencia()


class DolaresReferenciaIn(BaseModel):
    dolar_bna: float | None = Field(
        default=None, gt=0, description="Dólar Banco Nación ($); null = no tocar",
    )
    dolar_matba: float | None = Field(
        default=None, gt=0, description="Dólar Matba Rofex ($); null = no tocar",
    )


@router.patch("/agro/dolares-referencia")
def patch_dolares_referencia(
    payload: DolaresReferenciaIn,
    email: str = Depends(get_user_email),
    _mod: None = Depends(require_module("agro")),
    _noguest: None = Depends(require_no_invitado),   # escritura: bloquea invitado www (REGLA #8)
):
    """Upsert de los dólares Banco Nación / Matba Rofex (globales)."""
    try:
        return set_dolares_referencia(
            dolar_bna=payload.dolar_bna,
            dolar_matba=payload.dolar_matba,
            email=email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ─────────────────────────────────────────────────────────────────────────────
# MEJORAS PRECIO DISPONIBLE (Soja / Maíz / Trigo + LECAP)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/agro/mejoras-dispo")
def mejoras_dispo(
    _email: str = Depends(get_user_email),
    _engine: str | None = Query(None, include_in_schema=False),
):
    """3 bloques (Soja/Maíz/Trigo). Combina Cámara.precio_ars + TNA LECAPs +
    futuros DLR para mostrarle al productor cuánto cobra si se queda en
    LECAP + se cubre con futuro."""
    return _agro_svc(_engine).get_mejoras_dispo()
