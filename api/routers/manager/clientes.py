"""Manager sub-router — edición de Clientes.Comitentes (segmentación comercial).

Tab `/manager → CLIENTES`. Espejo del editor de Assets pero sobre el master
de clientes del Tablero Comercial (`Clientes.Comitentes`, ver
docs/TABLERO_COMERCIAL.md). Los campos de Aunesa (id_cuenta, denominacion,
operador, etc.) son READ-ONLY; se editan SOLO los 13 campos manuales de
segmentación.

Endpoints:
  GET   /api/manager/clientes         → lista filtrable (operador, nivel_1,
                                        campo_vacio, q). Default = todos.
  GET   /api/manager/clientes/values  → valores únicos para datalists/filtros.
  PATCH /api/manager/clientes         → edita campos manuales (id_cuenta en body).
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from core.mongo import get_mongo_client, get_mongo_client_read

router = APIRouter()

DB = "Clientes"
COL = "Comitentes"

_EMPTY_VALUES: list[str | None] = ["", None]

# Campos manuales editables (segmentación comercial). nivel_1..5 = árbol.
_EDITABLE_FIELDS: tuple[str, ...] = (
    "nivel_1", "nivel_2", "nivel_3", "nivel_4", "nivel_5",
    "primer_contacto_comercial", "riesgo_la_ft", "division",
    "adc", "dma", "observaciones", "sucursal", "referido",
)

# Campos de Aunesa que se muestran read-only (contexto para saber a quién editás).
_READONLY_FIELDS: tuple[str, ...] = (
    "denominacion", "tipo", "estado", "tipo_titular", "clase",
    "tipo_cliente", "perfil_inversion", "provincia",
    "operador_nombre", "operador_email",
)

_PROJECTION = {
    "_id": 0, "id_cuenta": 1,
    **{f: 1 for f in _READONLY_FIELDS},
    **{f: 1 for f in _EDITABLE_FIELDS},
    "actualizado_por": 1, "actualizado_at": 1,
}


def _normalize(rows: list[dict]) -> list[dict]:
    for a in rows:
        ts = a.get("actualizado_at")
        if isinstance(ts, datetime):
            a["actualizado_at"] = (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).isoformat()
    return rows


@router.get("/clientes")
def list_clientes(
    operador:    str | None = Query(None, description="Filtrar por operador_email exacto"),
    nivel_1:     str | None = Query(None, description="Filtrar por nivel_1 exacto"),
    campo_vacio: str | None = Query(None, description="Solo los que tienen ese campo manual vacío/null"),
    q:           str | None = Query(None, description="Búsqueda en id_cuenta o denominación"),
) -> dict:
    """Lista clientes de Clientes.Comitentes. Sin filtros: todo el master."""
    col = get_mongo_client_read()[DB][COL]
    filtros: list[dict] = []
    if operador:
        filtros.append({"operador_email": operador})
    if nivel_1:
        filtros.append({"nivel_1": nivel_1})
    if campo_vacio and campo_vacio in _EDITABLE_FIELDS:
        filtros.append({campo_vacio: {"$in": _EMPTY_VALUES}})
    if q and q.strip():
        rx = {"$regex": re.escape(q.strip()), "$options": "i"}
        filtros.append({"$or": [{"id_cuenta": rx}, {"denominacion": rx}]})

    query: dict = {"$and": filtros} if filtros else {}
    cur = col.find(query, _PROJECTION).sort("denominacion", 1).limit(5000)
    rows = _normalize(list(cur))
    return {"clientes": rows, "n": len(rows)}


@router.get("/clientes/values")
def get_clientes_values() -> dict:
    """Valores únicos por campo manual (para datalists) + operadores (filtro)."""
    col = get_mongo_client_read()[DB][COL]
    placeholders = {"", None}

    values: dict[str, list[str]] = {}
    for f in _EDITABLE_FIELDS:
        raw = col.distinct(f)
        values[f] = sorted({str(v) for v in raw if v not in placeholders})

    # Operadores: pares email→nombre para el filtro.
    pipeline = [
        {"$match": {"operador_email": {"$nin": list(placeholders)}}},
        {"$group": {"_id": "$operador_email", "nombre": {"$first": "$operador_nombre"}}},
        {"$sort": {"nombre": 1}},
    ]
    operadores = [
        {"email": d["_id"], "nombre": d.get("nombre") or d["_id"]}
        for d in col.aggregate(pipeline)
    ]
    return {"values": values, "operadores": operadores}


class _ClientePatch(BaseModel):
    """id_cuenta en el body. El resto son los manuales — solo se actualizan
    los que vengan (los `None` se ignoran)."""
    id_cuenta:                 str = Field(..., min_length=1, max_length=64)
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
def patch_cliente(
    req: _ClientePatch = Body(...),
    actor: str = Depends(get_user_email),
):
    """Update parcial de campos manuales. id_cuenta en el body. No crea
    cuentas (la cuenta tiene que existir — la pobla jobs.sync_comitentes)."""
    payload = req.model_dump(exclude_none=True)
    id_cuenta = payload.pop("id_cuenta")

    set_fields = {k: v for k, v in payload.items() if k in _EDITABLE_FIELDS}
    if not set_fields:
        raise HTTPException(400, "body sin campos editables — pasá al menos uno de "
                                 + ", ".join(_EDITABLE_FIELDS))

    set_fields["actualizado_por"] = actor
    set_fields["actualizado_at"] = datetime.now(UTC)

    col = get_mongo_client()[DB][COL]
    result = col.update_one({"id_cuenta": id_cuenta}, {"$set": set_fields})
    if result.matched_count == 0:
        raise HTTPException(404, f"id_cuenta no encontrada en Clientes.Comitentes: {id_cuenta!r}")

    doc = col.find_one({"id_cuenta": id_cuenta}, _PROJECTION) or {}
    return _normalize([doc])[0]
