"""api/routers/valuaciones_flujo.py — NEGOCIO → Valuaciones (flujo directo, SQL).

Gate: módulo `valuaciones-flujo` (solo admin + asistente_comercial) — aplicado en
api/main.py al montar. Endpoints:
  GET   /api/valuaciones-flujo/resumen?id_cuenta=X&desde=&hasta=  → matriz agregada.
  GET   /api/valuaciones-flujo/movimientos?id_cuenta=X&categoria=COMPRAS&...
        → detalle bajo demanda de una categoría (panel derecho).
  PATCH /api/valuaciones-flujo/seleccion → incluir/excluir un comprobante.
"""
from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import valuaciones_flujo as svc

router = APIRouter(prefix="/api/valuaciones-flujo", tags=["Valuaciones Flujo"])


@router.get("/resumen")
def resumen(
    id_cuenta: str = Query(..., min_length=1, description="id_cuenta"),
    desde: str | None = Query(None, description="YYYY-MM-DD (default 1-ene del año en curso)"),
    hasta: str | None = Query(None, description="YYYY-MM-DD (default hoy)"),
) -> dict:
    """Matriz mes × categoría (agregada en Postgres, solo incluidos)."""
    return svc.get_resumen(id_cuenta=id_cuenta, desde=desde, hasta=hasta)


@router.get("/movimientos")
def movimientos(
    id_cuenta: str = Query(..., min_length=1),
    categoria: str = Query(..., description="COMPRAS | VENTAS | RESCATES | SUSCRIPCIONES | OTROS"),
    desde: str | None = Query(None),
    hasta: str | None = Query(None),
    mes: str | None = Query(None, description="YYYY-MM (opcional, para una celda puntual)"),
) -> dict:
    """Detalle (bajo demanda) de una categoría — para el panel derecho 50%."""
    return svc.get_movimientos(id_cuenta=id_cuenta, categoria=categoria,
                               desde=desde, hasta=hasta, mes=mes)


@router.get("/mensual")
def mensual(id_cuenta: str = Query(..., min_length=1)) -> dict:
    """Tabla mensual estilo Carteras (Cierre/Flujo neto/Δ valor/PnL acum/TEM/TEA,
    ARS+USD) con el flujo = neto de los boletos incluidos."""
    return svc.get_mensual(id_cuenta=id_cuenta)


@router.get("/tenencias")
def tenencias(
    id_cuenta: str = Query(..., min_length=1),
    fecha: str | None = Query(None, description="YYYY-MM-DD (cierre del mes); default último"),
) -> dict:
    """Tenencias de la cuenta a la fecha de cierre (panel derecho inferior)."""
    return svc.get_tenencias(id_cuenta=id_cuenta, fecha=fecha)


class _SelPatch(BaseModel):
    id_cuenta:   str = Field(..., min_length=1)
    comprobante: str | int
    incluido:    bool


@router.patch("/seleccion")
def set_seleccion(req: _SelPatch = Body(...), actor: str = Depends(get_user_email)) -> dict:
    """Marca un comprobante incluido/excluido para la cuenta (guardado compartido)."""
    return svc.set_incluido(id_cuenta=req.id_cuenta, comprobante=req.comprobante,
                            incluido=req.incluido, actor=actor)
