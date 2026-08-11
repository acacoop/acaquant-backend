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
  …/lectores                          → allowlist de ACCESO a la vista Mesa de
                                        Dinero (quién la VE). Escribir implica
                                        leer, así que un escritor no hace falta
                                        que esté también acá.
  …/senebis-escritores               → ídem para la vista SENEBIS (allowlist
                                        propia: otro equipo, misma pantalla)
  …/tesoreria-escritores            → ídem para el SALDO INICIAL de Tesorería
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import mesa_dinero as _svc
from api.services import senebis as _svc_sen
from api.services import tesoreria as _svc_tes

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


# ── Lectores (allowlist de ACCESO a la vista Mesa de Dinero) ────────────────
# Mismo shape que los escritores (el panel del front es el mismo componente).
# Escribir implica leer → un escritor no necesita estar en esta lista.

@router.get("/mesa/lectores")
def list_lectores() -> dict:
    return _svc.listar_lectores()


@router.get("/mesa/lectores/candidatos")
def lectores_candidatos(q: str = Query("", description="Substring sobre email")) -> dict:
    return _svc.candidatos_lectores(q=q)


@router.post("/mesa/lectores")
def add_lector(req: _EscritorNew = Body(...), actor: str = Depends(get_user_email)) -> dict:
    try:
        return _svc.agregar_lector(req.email, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/mesa/lectores")
def delete_lector(email: str = Query(..., min_length=3),
                  actor: str = Depends(get_user_email)) -> dict:
    try:
        return _svc.quitar_lector(email, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ── Escritores de SENEBIS (allowlist propia, misma pantalla) ───────────────

@router.get("/mesa/senebis-escritores")
def list_escritores_senebis() -> dict:
    return _svc_sen.listar_escritores()


@router.get("/mesa/senebis-escritores/candidatos")
def escritores_senebis_candidatos(
    q: str = Query("", description="Substring sobre email"),
) -> dict:
    return _svc_sen.candidatos_escritores(q=q)


@router.post("/mesa/senebis-escritores")
def add_escritor_senebis(req: _EscritorNew = Body(...),
                         actor: str = Depends(get_user_email)) -> dict:
    try:
        return _svc_sen.agregar_escritor(req.email, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/mesa/senebis-escritores")
def delete_escritor_senebis(email: str = Query(..., min_length=3),
                            actor: str = Depends(get_user_email)) -> dict:
    try:
        return _svc_sen.quitar_escritor(email, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ── Escritores del SALDO INICIAL de Tesorería (allowlist propia) ───────────

@router.get("/mesa/tesoreria-escritores")
def list_escritores_tesoreria() -> dict:
    return _svc_tes.listar_escritores()


@router.get("/mesa/tesoreria-escritores/candidatos")
def escritores_tesoreria_candidatos(
    q: str = Query("", description="Substring sobre email"),
) -> dict:
    return _svc_tes.candidatos_escritores(q=q)


@router.post("/mesa/tesoreria-escritores")
def add_escritor_tesoreria(req: _EscritorNew = Body(...),
                           actor: str = Depends(get_user_email)) -> dict:
    try:
        return _svc_tes.agregar_escritor(req.email, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/mesa/tesoreria-escritores")
def delete_escritor_tesoreria(email: str = Query(..., min_length=3),
                              actor: str = Depends(get_user_email)) -> dict:
    try:
        return _svc_tes.quitar_escritor(email, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
