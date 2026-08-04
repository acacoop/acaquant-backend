"""Manager sub-router — telemetría de LATENCIA por endpoint.

Tab Manager → OBSERVABILIDAD → LATENCIA (reemplaza a la tab USO, decomisada).
Solo plumbing; la lógica vive en api/services/latencia_endpoints.py. Gate
`manager` (umbrella) en el __init__ del paquete.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from api.services import latencia_endpoints as svc

router = APIRouter()


@router.get("/latencia")
def latencia(
    horas: int = Query(24, ge=1, le=720, description="Ventana hacia atrás"),
    top: int = Query(50, ge=1, le=200, description="Máx endpoints en el ranking"),
) -> dict:
    """Ranking de endpoints por tiempo total consumido + serie horaria.

    Returns:
        {ventana_horas, endpoints: [{endpoint, n, avg_ms, max_ms, lentas,
         errores, pct_lentas}], serie: [{hora, n, avg_ms, max_ms}], total_requests}
    """
    return svc.get_latencia(horas=horas, top=top)
