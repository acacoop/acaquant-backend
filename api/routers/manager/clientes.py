"""Manager sub-router — edición de `clientes.comitentes` (segmentación comercial, SQL).

Tab `/manager → CLIENTES`. Thin HTTP plumbing: modelos Pydantic + Depends +
mapeo de errores. TODA la lógica (queries, bulk, cupos, re-clasificación)
vive en `api/services/clientes_admin_sql.py`.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import clientes_admin_sql as svc
from api.services.sin_operador import cuentas_sin_operador

router = APIRouter()
bulk_router = APIRouter()


@router.get("/clientes")
def list_clientes(
    operador:    str | None = Query(None, description="Filtrar por operador_email exacto"),
    nivel_1:     str | None = Query(None, description="Filtrar por nivel_1 exacto"),
    nivel_2:     str | None = Query(None, description="Filtrar por nivel_2 exacto"),
    nivel_3:     str | None = Query(None, description="Filtrar por nivel_3 exacto"),
    nivel_4:     str | None = Query(None, description="Filtrar por nivel_4 exacto"),
    nivel_5:     str | None = Query(None, description="Filtrar por nivel_5 exacto"),
    campo_vacio: str | None = Query(None, description="Solo los que tienen ese campo manual vacío/null"),
    q:           str | None = Query(None, description="Búsqueda en id_cuenta o denominación"),
) -> dict:
    """Lista clientes. Niveles combinados con AND; columnas por allowlist."""
    return svc.list_clientes(operador=operador, nivel_1=nivel_1, nivel_2=nivel_2,
                             nivel_3=nivel_3, nivel_4=nivel_4, nivel_5=nivel_5,
                             campo_vacio=campo_vacio, q=q)


@router.get("/clientes/values")
def get_clientes_values() -> dict:
    """Valores únicos por campo manual (datalists) + operadores + combos de niveles."""
    return svc.clientes_values()


@router.get("/clientes/sin-operador")
def clientes_sin_operador() -> dict:
    """Cuentas con volumen que caen en '(sin operador)' del ranking comercial."""
    return cuentas_sin_operador()


class _ClientePatch(BaseModel):
    id_cuenta:                 str = Field(..., min_length=1, max_length=64)
    operador_email:            str | None = Field(None, max_length=256)
    operador_nombre:           str | None = Field(None, max_length=256)
    nivel_1:                   str | None = Field(None, max_length=128)
    nivel_2:                   str | None = Field(None, max_length=128)
    nivel_3:                   str | None = Field(None, max_length=128)
    nivel_4:                   str | None = Field(None, max_length=128)
    nivel_5:                   str | None = Field(None, max_length=128)
    primer_contacto_comercial: str | None = Field(None, max_length=256)
    riesgo_la_ft:              str | None = Field(None, max_length=128)
    division:                  str | None = Field(None, max_length=128)
    adc:                       str | None = Field(None, max_length=128)
    dma:                       str | None = Field(None, max_length=128)
    observaciones:             str | None = Field(None, max_length=2000)
    sucursal:                  str | None = Field(None, max_length=128)
    referido:                  str | None = Field(None, max_length=256)


@router.patch("/clientes")
def patch_cliente(req: _ClientePatch = Body(...), actor: str = Depends(get_user_email)):
    """Update parcial de campos manuales + operador. id_cuenta en el body. No crea cuentas."""
    try:
        return svc.patch_cliente(req.model_dump(exclude_none=True), actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


class _BulkReq(BaseModel):
    rows: list[dict] = Field(..., max_length=20000)


@bulk_router.post("/clientes/bulk")
def bulk_clientes(req: _BulkReq, actor: str = Depends(get_user_email)):
    """Carga masiva: update por id_cuenta de SOLO los campos manuales + operador. No crea cuentas."""
    try:
        return svc.bulk_clientes(req.rows, actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class _BulkFondeoReq(BaseModel):
    rows: list[dict] = Field(..., max_length=20000)
    fuente: str | None = Field(None, max_length=128)


@bulk_router.post("/clientes/bulk-fondeo")
def bulk_clientes_fondeo(req: _BulkFondeoReq, actor: str = Depends(get_user_email)):
    """Carga masiva del cupo de fondeo del custodio (ARS). Re-clasifica nivel_3 al final."""
    try:
        return svc.bulk_fondeo(req.rows, req.fuente, actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class _RecalcReq(BaseModel):
    apply: bool = False


@bulk_router.post("/clientes/recalcular-niveles")
def recalcular_niveles(req: _RecalcReq, actor: str = Depends(get_user_email)):
    """Recalcula `nivel_3` de TODAS las comitentes activas. `apply=False` → preview."""
    return svc.recalcular_niveles(req.apply, actor)
