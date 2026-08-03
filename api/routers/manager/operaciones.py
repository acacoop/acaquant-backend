"""Manager · Operaciones — backfill de operaciones.operaciones (SQL) por CSV.

La UI (manager-view → tab OPERACIONES) parsea el CSV en el cliente y manda las
filas crudas (header→valor) en lotes. El backend normaliza + enriquece
(api/services/operaciones_informes.py) y escribe SQL. Admin-only (gate `manager`
en api/routers/manager/__init__.py). Tres modos:

  POST /operaciones/faltantes  → SOLO inserta boletos que no están (no pisa nada).
                                 commit=false previsualiza. Es el modo por defecto
                                 de la UI: tapar huecos del histórico.
  POST /operaciones/fechas     → corrige SOLO la columna concertacion contra el
                                 archivo (arregla el día/mes dado vuelta que dejó
                                 una carga histórica vieja). No toca nada más.
  POST /operaciones/backfill   → upsert por boleto (el Excel pisa lo que había).
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.services import operaciones_informes as svc
from core.postgres import get_pool

logger = logging.getLogger("api.manager.operaciones")

router = APIRouter()


class _BackfillReq(BaseModel):
    rows: list[dict] = Field(..., description="Filas crudas del CSV (header→valor).")
    crear_indice: bool = Field(
        False, description="(legacy, ignorado: el índice SQL ya existe).",
    )


class _FaltantesReq(BaseModel):
    rows: list[dict] = Field(..., description="Filas crudas del Excel (header→valor).")
    commit: bool = Field(False, description="false = previsualiza; true = inserta.")


@router.post("/operaciones/backfill")
def operaciones_backfill(req: _BackfillReq):
    """Normaliza + enriquece + upsertea un lote de filas en SQL operaciones."""
    if not req.rows:
        raise HTTPException(status_code=400, detail="Lote vacío.")
    try:
        # Enriquecimiento (mercado/operacion/segmento/nivel_3) igual que la ingesta
        # diaria — catálogo TiposOperacion SQL-native (operaciones.tipos_operacion).
        maps = svc.cargar_maps_enrich()
        return svc.ingestar_filas_sql(req.rows, enrich_maps=maps)
    except Exception as e:
        logger.exception("operaciones_backfill failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/operaciones/faltantes")
def operaciones_faltantes(req: _FaltantesReq):
    """Carga SOLO los boletos que faltan (no pisa los existentes).

    `commit=false` previsualiza: cuántos entran, rango de fechas, totales y qué
    boletos quedarían sin mercado/segmento. `commit=true` inserta.
    """
    if not req.rows:
        raise HTTPException(status_code=400, detail="Lote vacío.")
    try:
        maps = svc.cargar_maps_enrich()
        return svc.ingestar_faltantes_sql(req.rows, enrich_maps=maps, commit=req.commit)
    except Exception as e:
        logger.exception("operaciones_faltantes failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/operaciones/fechas")
def operaciones_fechas(req: _FaltantesReq):
    """Corrige SOLO `concertacion` de boletos ya cargados, contra el archivo.

    `commit=false` previsualiza: cuántas coinciden, cuántas tienen día y mes al
    revés, cuántas difieren de otra forma y qué boletos no existen en la base.
    `commit=true` aplica el UPDATE (una sola columna, idempotente).
    """
    if not req.rows:
        raise HTTPException(status_code=400, detail="Lote vacío.")
    try:
        return svc.corregir_fechas_sql(req.rows, commit=req.commit)
    except Exception as e:
        logger.exception("operaciones_fechas failed")
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
