"""Manager sub-router — Títulos → Instrumentos (solo lectura).

Vive separado de `checks.py` (admin-only) para poder gatearlo con el módulo
fino `manager_instrumentos` → así un rol comercial (asistente_comercial) puede
VER los instrumentos descubiertos de pyRofex SIN acceso a la edición de maestro
(Assets/ONs) ni a las tabs admin.

Lógica en `api/services/pyrofex_discovery_sql.py`. Los paths se conservan
(`/checks/...`) para no tocar el frontend.
"""
from __future__ import annotations

from fastapi import APIRouter

from api.services import pyrofex_discovery_sql as svc

router = APIRouter()


@router.get("/checks/discovery-pyrofex")
def discovery_pyrofex():
    """Instruments de pyRofex agrupados por CFI code (manager.pyrofex_discovery)."""
    return svc.discovery_pyrofex()


@router.get("/checks/instruments-by-cfi")
def instruments_by_cfi(cficode: str):
    """Detalle de todos los instruments de un CFI code (manager.pyrofex_instruments)."""
    return svc.instruments_by_cfi(cficode)
