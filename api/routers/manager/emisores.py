"""Manager sub-router — Títulos → Emisores (industria del emisor).

La industria es un atributo del EMISOR y no del bono: guardarla por bono es
escribir el mismo dato N veces y esperar que nadie lo escriba distinto (medido:
8 de 51 emisores tienen hoy sectores contradictorios entre sus propios bonos).
Mismo patrón que `mercado.rubros` para los CEDEARs: catálogo controlado, la
industria se elige o se crea explícita, nunca se escribe libre.

  GET   /api/manager/emisores               → catálogo + cuántos bonos cuelga cada uno
  GET   /api/manager/emisores/industrias    → catálogo controlado (dropdown)
  POST  /api/manager/emisores/industria     → crear una industria nueva
  PATCH /api/manager/emisores               → setear la industria de un emisor
  GET   /api/manager/emisores/pendientes    → lo que falta clasificar + las contradicciones
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from api.auth import get_user_email
from api.services import emisores as svc

router = APIRouter()


@router.get("/emisores")
def listar_emisores() -> dict:
    filas = svc.listar()
    return {"emisores": filas, "n": len(filas)}


@router.get("/emisores/industrias")
def listar_industrias() -> dict:
    return {"industrias": svc.industrias()}


class _IndustriaNueva(BaseModel):
    model_config = ConfigDict(extra="forbid")
    industria: str = Field(..., min_length=1, max_length=64)


@router.post("/emisores/industria")
def crear_industria(req: _IndustriaNueva = Body(...)) -> dict:
    try:
        return svc.crear_industria(req.industria)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class _SetIndustria(BaseModel):
    # `forbid` y no el default `ignore`: el front deploya a Vercel solo y SIEMPRE
    # antes que el backend, así que un campo nuevo contra un backend viejo se
    # descartaría con 200 OK — el operador clasifica y no pasa nada. Que falle.
    model_config = ConfigDict(extra="forbid")
    emisor: str = Field(..., min_length=1, max_length=128)
    # `None`/`""` deja al emisor SIN CLASIFICAR, que es un estado válido y visible.
    industria: str | None = Field(None, max_length=64)


@router.patch("/emisores")
def set_industria(req: _SetIndustria = Body(...),
                  actor: str = Depends(get_user_email)) -> dict:
    try:
        return svc.set_industria(req.emisor, req.industria, actor=actor or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/emisores/pendientes")
def pendientes() -> dict:
    """Lo que falta decidir. Se sirve JUNTO con las contradicciones a propósito:
    son la misma tarea — los emisores con sectores que se contradicen son
    exactamente los que necesitan que un humano elija cuál queda."""
    falta = svc.sin_clasificar()
    choques = svc.contradicciones()
    return {
        "sin_clasificar": falta,
        "contradicciones": choques,
        "n_sin_clasificar": len(falta),
        "n_contradicciones": len(choques),
    }
