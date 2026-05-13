"""Router /api/scanner — vista Scanner del módulo Renta Variable.

Consumido por el frontend acaquant-web /renta-variable (tab Scanner).
RBAC `renta-variable` — actualmente admin-only (Manager.RoleMatrix).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth import require_module
from api.services import scanner as svc

router = APIRouter(
    prefix="/api/scanner",
    tags=["Scanner"],
    dependencies=[Depends(require_module("renta-variable"))],
)


@router.get("/cedears")
def cedears_scanner():
    """Lista de CEDEARs activos con master + snapshot live join.

    Returns:
        list[dict] con shape:
            ticker_corto, underlying, ratio_cedear,
            sector, industria, region, pais,
            last, open, high, low, close,
            intraday_pct, vs_1d_pct, vs_1d_usd_pct,
            updated_at
    """
    return svc.get_cedears_scanner()


@router.get("/ccl")
def ccl_live():
    """CCL live + variación 1D — para el KPI del shell de Renta Variable.

    Returns:
        {value: float|None, vs_1d_pct: float|None, ts: str|None}
    """
    return svc.get_ccl_live()
