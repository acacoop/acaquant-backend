"""Router /api/derivados/sinteticos — sintéticos LECAP / DLK + futuro DLR.

Lectura pura: el service matchea solo (por año-mes de vencimiento) y devuelve
las dos tablas; el endpoint es un thin wrapper. Acceso por el gate genérico
de derivados (`/api/derivados/*`, abierto a los 3 roles).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth import get_user_email
from api.services.sinteticos import get_sinteticos

router = APIRouter(prefix="/api/derivados", tags=["DerivadosSinteticos"])


@router.get("/sinteticos")
def sinteticos(_email: str = Depends(get_user_email)):
    """Devuelve las dos tablas (long-lecap / short-dlk) con todos los
    instrumentos que matcheen contra un futuro DLR vigente."""
    return get_sinteticos()
