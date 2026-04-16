"""Router Operaciones: endpoints para MesaAPI (flujo de contrapartes)."""
from fastapi import APIRouter, Query

from api.deps import get_db_operaciones

router = APIRouter(prefix="/api/operaciones", tags=["Operaciones"])


@router.get("/flujo")
def listar_flujo(
    contraparte: str | None = Query(None, description="Filtrar por contraparte"),
    moneda: str | None = Query(None, description="Filtrar por moneda (ARS/USD)"),
    segmento: str | None = Query(None, description="Filtrar por segmento"),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    db = get_db_operaciones()
    filtro = {}
    if contraparte:
        filtro["contraparte"] = contraparte
    if moneda:
        filtro["moneda"] = moneda
    if segmento:
        filtro["segmento"] = segmento
    if desde or hasta:
        rango = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["concertacion"] = rango

    docs = list(db["MesaAPI"].find(filtro, {"_id": 0}))
    return docs
