"""Router /api/simulaciones — carteras hipotéticas por usuario.

CRUD básico. Cada usuario solo ve y muta sus propias simulaciones (filtro
por user_email en el service). El cálculo de cashflows / métricas / composición
se expone aparte en POST /api/simulaciones/calcular (próximamente PR2).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services.simulaciones import (
    Posicion,
    Simulacion,
    SimulacionCreate,
    SimulacionUpdate,
    actualizar_simulacion,
    calcular,
    crear_simulacion,
    eliminar_simulacion,
    listar_simulaciones,
    listar_tickers_disponibles,
    obtener_simulacion,
)

router = APIRouter(prefix="/api/simulaciones", tags=["simulaciones"])
logger = logging.getLogger(__name__)


@router.get("", response_model=list[Simulacion])
def listar(email: str = Depends(get_user_email)) -> list[Simulacion]:
    """Todas las simulaciones del usuario, más recientes primero."""
    return listar_simulaciones(email)


@router.post("", response_model=Simulacion, status_code=201)
def crear(
    data: SimulacionCreate,
    email: str = Depends(get_user_email),
) -> Simulacion:
    """Crea una nueva simulación con nombre + posiciones iniciales (puede
    venir vacío si el usuario quiere empezar a cargar tickers después)."""
    return crear_simulacion(email, data)


# Rutas ESTÁTICAS antes que las dinámicas (`/{simulacion_id}`) para que
# FastAPI no las confunda con un id inválido.


@router.get("/tickers")
def tickers(_email: str = Depends(get_user_email)) -> list[dict[str, Any]]:
    """Universo de tickers disponibles para el autocomplete del frontend.

    Lista plana de Valuaciones.Assets enriquecida con curva/tipo desde
    Trading.Curvas cuando existe (no todos los tickers tienen entrada en
    Curvas, ej. FCI). Sin filtro por usuario — el universo es público
    dentro de la mesa.
    """
    return listar_tickers_disponibles()


@router.get("/{simulacion_id}", response_model=Simulacion)
def obtener(
    simulacion_id: str,
    email: str = Depends(get_user_email),
) -> Simulacion:
    """Devuelve una simulación. 404 si no existe o pertenece a otro usuario
    (no se distingue uno del otro a propósito — no leakeamos existencia)."""
    sim = obtener_simulacion(simulacion_id, email)
    if sim is None:
        raise HTTPException(status_code=404, detail="simulación no encontrada")
    return sim


@router.put("/{simulacion_id}", response_model=Simulacion)
def actualizar(
    simulacion_id: str,
    data: SimulacionUpdate,
    email: str = Depends(get_user_email),
) -> Simulacion:
    """PATCH semantics: solo se actualizan los campos que vienen no-None."""
    sim = actualizar_simulacion(simulacion_id, email, data)
    if sim is None:
        raise HTTPException(status_code=404, detail="simulación no encontrada")
    return sim


@router.delete("/{simulacion_id}", status_code=204)
def eliminar(
    simulacion_id: str,
    email: str = Depends(get_user_email),
) -> None:
    """204 si se eliminó. 404 si no existía o pertenecía a otro usuario."""
    if not eliminar_simulacion(simulacion_id, email):
        raise HTTPException(status_code=404, detail="simulación no encontrada")


# ─────────────────────────────────────────────────────────────────────────────
# Cálculo (preview live, sin guardar)
# ─────────────────────────────────────────────────────────────────────────────


class CalcularRequest(BaseModel):
    """Body de POST /api/simulaciones/calcular.

    No requiere `nombre` ni id — el cálculo es stateless. El frontend lo
    invoca tanto al cargar una simulación guardada como al editar en vivo.
    """

    posiciones: list[Posicion] = Field(default_factory=list)


@router.post("/calcular")
def calcular_endpoint(
    req: CalcularRequest,
    _email: str = Depends(get_user_email),  # solo para auth, no se usa
) -> dict[str, Any]:
    """Devuelve analytics de una cartera: cashflows + composición + métricas.

    No persiste. No requiere que la cartera esté guardada. Está protegido
    por auth para evitar uso anónimo.

    Response shape: ver `api.services.simulaciones.calcular`.
    """
    return calcular(req.posiciones)
