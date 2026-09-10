"""Router /api/fci — vista FONDOS COMUNES DE INVERSIÓN (`/fci` en acaquant-web).

Doc madre: `docs/FCI.md`. Read-only. Gate a nivel router: módulo `fci`
(`core/roles.py::MODULES`). NO está en `INVITADO_MODULES` (default-deny,
REGLA #8): abrirlo al portal www es una decisión aparte, de una línea.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path

from api.auth import require_module
from api.services import fci_sql as svc

router = APIRouter(
    prefix="/api/fci",
    tags=["FCI"],
    dependencies=[Depends(require_module("fci"))],
)


@router.get("/tabla")
def tabla():
    """Los fondos del universo (gerentes seguidas) con VCP y rendimientos
    1D/WTD/MTD/YTD/7D/30D/90D/365D (fracciones) + TNA 7D/30D, más los catálogos
    de filtros (categorías, gerentes, monedas)."""
    return svc.tabla()


@router.get("/fondo/{fci_id}")
def fondo(fci_id: int = Path(..., ge=1)):
    """Ficha de un fondo: la fila de la tabla + serie de VCP (400 días, con la
    fuente de cada punto) + el asset de Manager si está linkeado."""
    out = svc.ficha(fci_id=fci_id)
    if out is None:
        raise HTTPException(404, f"fondo {fci_id} no está en el universo FCI")
    return out
