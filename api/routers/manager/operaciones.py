"""Manager · Operaciones — backfill de CashFlow.Operaciones por CSV.

La UI (manager-view → tab OPERACIONES) parsea el CSV en el cliente y manda las
filas crudas (header→valor) en lotes a `POST /operaciones/backfill`. El backend
normaliza (api/services/operaciones_informes.py) y upsertea por boleto. Admin-only
(gate `manager` en api/routers/manager/__init__.py).
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.services import operaciones_informes as svc
from core.mongo import get_mongo_client, get_mongo_client_read

logger = logging.getLogger("api.manager.operaciones")

router = APIRouter()

_DB = "CashFlow"
_COL = "Operaciones"


class _BackfillReq(BaseModel):
    rows: list[dict] = Field(..., description="Filas crudas del CSV (header→valor).")
    crear_indice: bool = Field(
        False, description="Crear el índice único (mandar True en el primer lote).",
    )


@router.post("/operaciones/backfill")
def operaciones_backfill(req: _BackfillReq):
    """Normaliza + upsertea un lote de filas en CashFlow.Operaciones."""
    if not req.rows:
        raise HTTPException(status_code=400, detail="Lote vacío.")
    try:
        # Cliente RW (PRIMARY): el read-only no puede escribir ni crear índices.
        coll = get_mongo_client()[_DB][_COL]
        return svc.ingestar_filas(coll, req.rows, crear_indice=req.crear_indice)
    except Exception as e:
        logger.exception("operaciones_backfill failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/operaciones/stats")
def operaciones_stats():
    """Estado actual de CashFlow.Operaciones (para mostrar en la UI)."""
    try:
        return svc.stats(get_mongo_client_read()[_DB][_COL])
    except Exception as e:
        logger.exception("operaciones_stats failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
