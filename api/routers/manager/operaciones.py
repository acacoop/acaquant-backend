"""Manager · Operaciones — backfill de operaciones.operaciones (SQL) por CSV.

La UI (manager-view → tab OPERACIONES) parsea el CSV en el cliente y manda las
filas crudas (header→valor) en lotes a `POST /operaciones/backfill`. El backend
normaliza + enriquece (api/services/operaciones_informes.py) y upsertea por boleto
en SQL. Admin-only (gate `manager` en api/routers/manager/__init__.py).
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.services import operaciones_informes as svc
from core.mongo import get_mongo_client
from core.postgres import get_pool

logger = logging.getLogger("api.manager.operaciones")

router = APIRouter()


class _BackfillReq(BaseModel):
    rows: list[dict] = Field(..., description="Filas crudas del CSV (header→valor).")
    crear_indice: bool = Field(
        False, description="(legacy, ignorado: el índice SQL ya existe).",
    )


@router.post("/operaciones/backfill")
def operaciones_backfill(req: _BackfillReq):
    """Normaliza + enriquece + upsertea un lote de filas en SQL operaciones."""
    if not req.rows:
        raise HTTPException(status_code=400, detail="Lote vacío.")
    try:
        # Enriquecimiento (mercado/operacion/segmento/nivel_3) igual que la ingesta
        # diaria — el catálogo TiposOperacion sigue en Mongo (chico).
        maps = svc.cargar_maps_enrich(get_mongo_client()["CashFlow"])
        return svc.ingestar_filas_sql(req.rows, enrich_maps=maps)
    except Exception as e:
        logger.exception("operaciones_backfill failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/operaciones/stats")
def operaciones_stats():
    """Estado actual de operaciones.operaciones (SQL) para mostrar en la UI."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n, count(DISTINCT id_cuenta) AS n_cuentas, "
                "min(concertacion)::text AS min_c, max(concertacion)::text AS max_c "
                "FROM operaciones")
            n, n_cuentas, min_c, max_c = cur.fetchone()
        return {"n": n, "n_cuentas": n_cuentas,
                "min_concertacion": min_c, "max_concertacion": max_c}
    except Exception as e:
        logger.exception("operaciones_stats failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
