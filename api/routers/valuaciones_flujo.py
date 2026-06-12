"""api/routers/valuaciones_flujo.py — NEGOCIO → Valuaciones (flujo directo).

Gate: módulo `valuaciones-flujo` (solo admin + asistente_comercial) — aplicado en
api/main.py al montar (require_module). Endpoints:
  GET   /api/valuaciones-flujo/movimientos?id_cuenta=X  → flujo de la cuenta.
  PATCH /api/valuaciones-flujo/seleccion                → incluir/excluir un mov.
"""
from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import valuaciones_flujo as svc

router = APIRouter(prefix="/api/valuaciones-flujo", tags=["Valuaciones Flujo"])


@router.get("/movimientos")
def movimientos(id_cuenta: str = Query(..., min_length=1, description="id_cuenta")) -> dict:
    """Movimientos de títulos de la cuenta + flag `incluido` (default todos sí)."""
    return svc.get_flujo(id_cuenta=id_cuenta)


class _SelPatch(BaseModel):
    id_cuenta:   str = Field(..., min_length=1)
    comprobante: str | int
    incluido:    bool


@router.patch("/seleccion")
def set_seleccion(req: _SelPatch = Body(...), actor: str = Depends(get_user_email)) -> dict:
    """Marca un comprobante incluido/excluido para la cuenta (guardado compartido)."""
    return svc.set_incluido(id_cuenta=req.id_cuenta, comprobante=req.comprobante,
                            incluido=req.incluido, actor=actor)
