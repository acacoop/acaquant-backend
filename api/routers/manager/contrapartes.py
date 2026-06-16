"""Manager sub-router — vista CONTRAPARTES (módulo `manager_contrapartes`).

Tab `/manager → CONTRAPARTES`, partida en dos:
  - IZQUIERDA (segmentar): lista CashFlow.Contrapartes y edita `contraparte` + `segmento`
    (cuenta + denominacion vienen de Aunesa, read-only).
  - DERECHA (conciliador): "Solicitar cuentas" → cuentas de Aunesa que no están en
    Contrapartes y cuya denominacion matchea un nombre de contraparte → alta de 1 click.

Endpoints (prefix /api/manager lo agrega el paquete):
  GET   /contrapartes            → lista filtrable (segmento, contraparte, q). Dual-engine.
  GET   /contrapartes/segmentos  → valores distintos (autocomplete). Dual-engine.
  PATCH /contrapartes            → edita contraparte/segmento (cuenta en body). Mongo.
  POST  /contrapartes            → alta desde el conciliador (idempotente). Mongo.
  GET   /contrapartes/reconcile  → conciliador (pega Aunesa live, on-demand). Mongo.

Fuente única SQL `clientes.contrapartes` (lecturas, escrituras y conciliador).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from api.auth import get_user_email
from api.services import contrapartes_seg as _svc

logger = logging.getLogger("api.manager.contrapartes")
router = APIRouter()


@router.get("/contrapartes")
def list_contrapartes(
    segmento: str | None = Query(None, description="Filtra por segmento exacto"),
    contraparte: str | None = Query(None, description="Filtra por contraparte exacta"),
    q: str | None = Query(None, description="Substring sobre denominacion o cuenta"),
) -> dict:
    """Lista de contrapartes para el panel de segmentación."""
    return _svc.listar_contrapartes(segmento=segmento, contraparte=contraparte, q=q)


@router.get("/contrapartes/segmentos")
def contrapartes_segmentos() -> dict:
    """Valores distintos de segmento + contraparte (datalist del form)."""
    return _svc.segmentos_distinct()


class _ContrapartePatch(BaseModel):
    """cuenta va en el body (evita URL-encoding); contraparte/segmento opcionales.
    coerce_numbers_to_str: hay docs sucios con cuenta/contraparte numéricos (CUIT) →
    si el front los reenvía como número, se coercen a string en vez de tirar 422."""
    model_config = ConfigDict(coerce_numbers_to_str=True)
    cuenta:      str = Field(..., min_length=1, max_length=128)
    contraparte: str | None = Field(None, max_length=512)
    segmento:    str | None = Field(None, max_length=256)


@router.patch("/contrapartes")
def patch_contraparte(req: _ContrapartePatch = Body(...), actor: str = Depends(get_user_email)) -> dict:
    """Edita contraparte/segmento de una cuenta existente."""
    res = _svc.update_contraparte(
        cuenta=req.cuenta, contraparte=req.contraparte, segmento=req.segmento, actor=actor)
    if not res.get("updated"):
        reason = res.get("reason")
        if reason == "not_found":
            raise HTTPException(404, f"cuenta no encontrada en Contrapartes: {req.cuenta!r}")
        raise HTTPException(400, "body sin campos editables — pasá contraparte y/o segmento.")
    return res


class _ContraparteNew(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    cuenta:       str = Field(..., min_length=1, max_length=128)
    denominacion: str | None = Field(None, max_length=512)
    contraparte:  str | None = Field(None, max_length=512)
    segmento:     str | None = Field(None, max_length=256)


@router.post("/contrapartes")
def add_contraparte(req: _ContraparteNew = Body(...), actor: str = Depends(get_user_email)) -> dict:
    """Alta de 1 click desde el conciliador (idempotente)."""
    res = _svc.add_contraparte(
        cuenta=req.cuenta, denominacion=req.denominacion, contraparte=req.contraparte,
        segmento=req.segmento, actor=actor)
    if not res.get("added"):
        raise HTTPException(400, "cuenta requerida.")
    return res


@router.get("/contrapartes/reconcile")
def reconcile_contrapartes(
    limit: int = Query(500, ge=1, le=2000, description="Máximo de candidatos a devolver"),
) -> dict:
    """Conciliador: pega Aunesa LIVE. On-demand (botón) — no cachear ni pollear."""
    try:
        return _svc.reconciliar(limit=limit)
    except Exception as e:
        logger.exception("reconcile_contrapartes failed")
        raise HTTPException(502, f"No se pudo conciliar con Aunesa: {e}") from e
