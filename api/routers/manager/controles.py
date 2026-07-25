"""GET /api/manager/controles — anomalías del auto-control de calidad de datos.

Detalle completo de lo que jobs/controles_datos detecta: assets sin cartera,
comitentes sin nivel_1, contrapartes sin alta, forwards faltantes, etc.
Gate admin (`manager`), igual que Diagnóstico.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from api.services.controles_sql import listar_controles

router = APIRouter()


@router.get("/controles")
def get_controles(
    resueltos_dias: int = Query(7, ge=0, le=90,
                                description="Incluir resueltos de los últimos N días"),
) -> dict:
    return listar_controles(incluir_resueltos_dias=resueltos_dias)
