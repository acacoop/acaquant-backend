"""Manager sub-router — MESA (gestión de Mesa de Dinero, módulo `manager`).

Tab `/manager → MESA`: catálogo de TRADERS (los nombres válidos del campo
Trader en /mesa-dinero) + allowlist de ESCRITORES (usuarios de la app con
permiso de escritura en la vista). Admin-only (umbrella `manager`).

Endpoints (prefix /api/manager lo agrega el paquete):
  GET    /mesa/traders                → catálogo de traders
  POST   /mesa/traders                → alta por nombre (idempotente)
  DELETE /mesa/traders                → baja por nombre
  GET    /mesa/escritores             → allowlist de escritura
  GET    /mesa/escritores/candidatos  → usuarios de la app para agregar (q)
  POST   /mesa/escritores             → alta por email (idempotente)
  DELETE /mesa/escritores             → baja por email
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import mesa_dinero as _svc

router = APIRouter()


# ── Traders ──────────────────────────────────────────────────────────────────

@router.get("/mesa/traders")
def list_traders() -> dict:
    return _svc.listar_traders()


class _TraderNew(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=128)


@router.post("/mesa/traders")
def add_trader(req: _TraderNew = Body(...), actor: str = Depends(get_user_email)) -> dict:
    try:
        return _svc.agregar_trader(req.nombre, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/mesa/traders")
def delete_trader(nombre: str = Query(..., min_length=1),
                  actor: str = Depends(get_user_email)) -> dict:
    try:
        return _svc.quitar_trader(nombre, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ── Escritores (allowlist de escritura de la vista) ──────────────────────────

@router.get("/mesa/escritores")
def list_escritores() -> dict:
    return _svc.listar_escritores()


@router.get("/mesa/escritores/candidatos")
def escritores_candidatos(q: str = Query("", description="Substring sobre email")) -> dict:
    return _svc.candidatos_escritores(q=q)


class _EscritorNew(BaseModel):
    email: str = Field(..., min_length=3, max_length=256)


@router.post("/mesa/escritores")
def add_escritor(req: _EscritorNew = Body(...), actor: str = Depends(get_user_email)) -> dict:
    try:
        return _svc.agregar_escritor(req.email, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/mesa/escritores")
def delete_escritor(email: str = Query(..., min_length=3),
                    actor: str = Depends(get_user_email)) -> dict:
    try:
        return _svc.quitar_escritor(email, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
