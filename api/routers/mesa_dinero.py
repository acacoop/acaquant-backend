"""Router MESA DE DINERO — /api/mesa-dinero (vista NEGOCIO → /mesa-dinero).

LECTURA: gate módulo `operaciones` (se monta en api/main.py con _OPERACIONES,
igual que el resto de NEGOCIO). ESCRITURA: además del módulo, allowlist
per-usuario `operaciones.mesa_dinero_escritores` (editable en Manager → MESA)
+ admin — enforcement server-side en cada endpoint de write (default-deny).

Thin HTTP plumbing: la lógica vive en api/services/mesa_dinero.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import mesa_dinero as _svc

router = APIRouter(prefix="/api/mesa-dinero", tags=["Mesa de Dinero"])


def _exigir_escritura(actor: str) -> None:
    if not _svc.puede_escribir(actor):
        raise HTTPException(403, "sin permiso de escritura en Mesa de Dinero")


# ── Lectura ──────────────────────────────────────────────────────────────────

@router.get("/ops")
def listar_ops(
    desde: str | None = Query(None, description="YYYY-MM-DD"),
    hasta: str | None = Query(None, description="YYYY-MM-DD"),
    trader: str | None = Query(None, description="filtrar por trader exacto"),
) -> dict:
    return _svc.listar_ops(desde=desde, hasta=hasta, trader=trader)


@router.get("/resumen")
def resumen(
    desde: str | None = Query(None, description="YYYY-MM-DD"),
    hasta: str | None = Query(None, description="YYYY-MM-DD"),
    trader: str | None = Query(None, description="filtrar por trader exacto"),
) -> dict:
    return _svc.resumen(desde=desde, hasta=hasta, trader=trader)


@router.get("/resultados")
def resultados(
    desde: str | None = Query(None, description="YYYY-MM-DD"),
    hasta: str | None = Query(None, description="YYYY-MM-DD"),
    trader: str | None = Query(None, description="filtrar por trader exacto"),
) -> dict:
    return _svc.resultados(desde=desde, hasta=hasta, trader=trader)


@router.get("/opciones")
def opciones(actor: str = Depends(get_user_email)) -> dict:
    return _svc.opciones(email=actor)


# ── Escritura (allowlist + admin) ────────────────────────────────────────────

class _OpPayload(BaseModel):
    fecha: str = Field(..., min_length=10, max_length=10, description="YYYY-MM-DD")
    trader: str = Field(..., min_length=1, max_length=128)
    activo: str | None = Field(None, max_length=128)
    vn_compra: float | None = None
    px_compra: float | None = None
    vn_venta: float | None = None
    px_venta: float | None = None
    # Solo para registros SIN patas (ej. "Pase OPS"); con patas se deriva y se ignora.
    resultado: float | None = None
    cliente: str | None = Field(None, max_length=256)
    observacion: str | None = Field(None, max_length=128)


@router.post("/ops")
def crear_op(req: _OpPayload = Body(...), actor: str = Depends(get_user_email)) -> dict:
    _exigir_escritura(actor)
    try:
        return _svc.crear_op(req.model_dump(), actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.patch("/ops/{op_id}")
def editar_op(op_id: int, req: _OpPayload = Body(...),
              actor: str = Depends(get_user_email)) -> dict:
    _exigir_escritura(actor)
    try:
        return _svc.editar_op(op_id, req.model_dump(), actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/ops/{op_id}")
def borrar_op(op_id: int, actor: str = Depends(get_user_email)) -> dict:
    _exigir_escritura(actor)
    try:
        return _svc.borrar_op(op_id, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class _TcPayload(BaseModel):
    fecha: str = Field(..., min_length=10, max_length=10, description="YYYY-MM-DD")
    tc: float = Field(..., gt=0)


@router.put("/tc")
def set_tc(req: _TcPayload = Body(...), actor: str = Depends(get_user_email)) -> dict:
    _exigir_escritura(actor)
    try:
        return _svc.set_tc(req.fecha, req.tc, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
