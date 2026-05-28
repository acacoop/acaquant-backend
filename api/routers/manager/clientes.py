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
from pymongo import UpdateOne

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

# Operador (campos de Aunesa) que la CARGA MASIVA puede sobrescribir desde el
# Excel — corrección manual de la mesa. A propósito NO están en _EDITABLE_FIELDS:
# en la edición fila-por-fila siguen read-only; solo se corrigen por bulk. El
# sync nocturno ya no los pisa (INSERT_ONLY_FIELDS en jobs/sync_comitentes.py),
# así que la corrección sobrevive el cron.
_BULK_OPERADOR_FIELDS: tuple[str, ...] = ("operador_email", "operador_nombre")

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
    """id_cuenta en el body. El resto son los manuales + operador — solo se
    actualizan los que vengan (los `None` se ignoran)."""
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
def patch_cliente(
    req: _ClientePatch = Body(...),
    actor: str = Depends(get_user_email),
):
    """Update parcial de campos manuales + operador (edición inline). id_cuenta
    en el body. No crea cuentas (la cuenta existe — la pobla jobs.sync_comitentes)."""
    payload = req.model_dump(exclude_none=True)
    id_cuenta = payload.pop("id_cuenta")

    allowed = set(_EDITABLE_FIELDS) | set(_BULK_OPERADOR_FIELDS)
    set_fields = {k: v for k, v in payload.items() if k in allowed}
    if not set_fields:
        raise HTTPException(400, "body sin campos editables — pasá al menos uno de "
                                 + ", ".join((*_EDITABLE_FIELDS, *_BULK_OPERADOR_FIELDS)))

    set_fields["actualizado_por"] = actor
    set_fields["actualizado_at"] = datetime.now(UTC)

    col = get_mongo_client()[DB][COL]
    result = col.update_one({"id_cuenta": id_cuenta}, {"$set": set_fields})
    if result.matched_count == 0:
        raise HTTPException(404, f"id_cuenta no encontrada en Clientes.Comitentes: {id_cuenta!r}")

    doc = col.find_one({"id_cuenta": id_cuenta}, _PROJECTION) or {}
    return _normalize([doc])[0]


class _BulkReq(BaseModel):
    """Filas parseadas de un archivo (csv/xlsx) en el frontend. Cada row es
    {id_cuenta, <campo_manual>: valor, ...}. Solo se aplican los campos
    manuales reconocidos; el resto se ignora."""
    rows: list[dict] = Field(..., max_length=20000)


@router.post("/clientes/bulk")
def bulk_clientes(req: _BulkReq, actor: str = Depends(get_user_email)):
    """Carga masiva desde archivo. Update por id_cuenta de SOLO los campos
    manuales (_EDITABLE_FIELDS) + el operador (_BULK_OPERADOR_FIELDS) presentes y
    no vacíos. No crea cuentas."""
    now = datetime.now(UTC)
    ops: list[UpdateOne] = []
    ids: list[str] = []
    sin_id = sin_campos = 0
    bulk_cols = set(_EDITABLE_FIELDS) | set(_BULK_OPERADOR_FIELDS)

    for row in req.rows:
        id_cuenta = str(row.get("id_cuenta") or "").strip()
        if not id_cuenta:
            sin_id += 1
            continue
        set_fields: dict = {}
        for k, v in row.items():
            if k in bulk_cols:
                val = ("" if v is None else str(v)).strip()
                if val != "":
                    set_fields[k] = val
        if not set_fields:
            sin_campos += 1
            continue
        set_fields["actualizado_por"] = actor
        set_fields["actualizado_at"] = now
        ops.append(UpdateOne({"id_cuenta": id_cuenta}, {"$set": set_fields}))
        ids.append(id_cuenta)

    if not ops:
        raise HTTPException(400, "no hay filas válidas (falta id_cuenta o columnas con datos)")

    col = get_mongo_client()[DB][COL]
    res = col.bulk_write(ops, ordered=False)
    existentes = set(col.distinct("id_cuenta", {"id_cuenta": {"$in": ids}}))
    no_encontradas = sorted(set(ids) - existentes)
    return {
        "actualizadas":     res.modified_count,
        "matched":          res.matched_count,
        "filas_validas":    len(ops),
        "sin_id":           sin_id,
        "sin_campos":       sin_campos,
        "n_no_encontradas": len(no_encontradas),
        "no_encontradas":   no_encontradas[:50],
    }


# ── Carga masiva de LÍMITES DE FONDEO ─────────────────────────────────────
# Subdoc `limite_fondeo` en Clientes.Comitentes. Lo consume el motor de
# segmentación patrimonial. Ver docs/SEGMENTACION_PATRIMONIAL.md.

def _parse_num(v) -> float | None:
    """Tolera number, '1234.56', '1.234.567,89' (formato AR) y vacío.
    Devuelve None si no es numérico parseable o si está vacío."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    # "1.234.567,89" (AR) → "1234567.89". Si tiene coma decimal: quitar puntos
    # de miles y reemplazar la coma por punto. Si solo hay puntos, asumir
    # punto decimal estándar.
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


class _BulkFondeoReq(BaseModel):
    """Filas de un CSV/XLSX parseado en el frontend. Cada row es
    {id_cuenta, limite_disponible, limite_utilizado}. `fuente` es una etiqueta
    libre para trazar de dónde vino (ej. nombre del archivo)."""
    rows: list[dict] = Field(..., max_length=20000)
    fuente: str | None = Field(None, max_length=128)


@router.post("/clientes/bulk-fondeo")
def bulk_clientes_fondeo(req: _BulkFondeoReq, actor: str = Depends(get_user_email)):
    """Carga masiva del límite de fondeo del custodio (ARS).

    Semántica idéntica al bulk de segmentación: itera fila por fila, no crea
    cuentas, NO toca cuentas ausentes del payload (subir 10 filas toca solo
    esas 10), idempotente al re-subir (reemplaza vía $set). Si vienen los dos
    montos en la misma fila se computa `utilizacion_pct`; si viene solo uno,
    se escribe solo ese y el pct se borra (queda inconsistente hasta la
    próxima carga completa)."""
    now = datetime.now(UTC)
    fuente = (req.fuente or "manager_bulk").strip()[:128]
    ops: list[UpdateOne] = []
    ids: list[str] = []
    sin_id = sin_campos = sin_numeros = 0

    for row in req.rows:
        id_cuenta = str(row.get("id_cuenta") or "").strip()
        if not id_cuenta:
            sin_id += 1
            continue

        raw_disp = row.get("limite_disponible")
        raw_util = row.get("limite_utilizado")
        disp = _parse_num(raw_disp)
        util = _parse_num(raw_util)

        # id pero ambos vacíos → no escribir (sin_campos).
        if raw_disp in (None, "") and raw_util in (None, ""):
            sin_campos += 1
            continue
        # Algún valor venía pero no es parseable → no escribir (sin_numeros).
        if (raw_disp not in (None, "") and disp is None) or (
            raw_util not in (None, "") and util is None
        ):
            sin_numeros += 1
            continue

        set_fields: dict = {
            "limite_fondeo.cargado_en": now,
            "limite_fondeo.fuente":     fuente,
            "actualizado_por":          actor,
            "actualizado_at":           now,
        }
        unset_fields: dict = {}

        if disp is not None:
            set_fields["limite_fondeo.disponible_ars"] = disp
        if util is not None:
            set_fields["limite_fondeo.utilizado_ars"] = util
        if disp is not None and util is not None and disp > 0:
            set_fields["limite_fondeo.utilizacion_pct"] = round(util / disp * 100, 2)
        else:
            # Vino solo uno (o disp=0) → pct queda stale; mejor borrarlo.
            unset_fields["limite_fondeo.utilizacion_pct"] = ""

        update: dict = {"$set": set_fields}
        if unset_fields:
            update["$unset"] = unset_fields

        ops.append(UpdateOne({"id_cuenta": id_cuenta}, update))
        ids.append(id_cuenta)

    if not ops:
        raise HTTPException(
            400,
            "no hay filas válidas (falta id_cuenta o ambos límites vacíos/no numéricos)",
        )

    col = get_mongo_client()[DB][COL]
    res = col.bulk_write(ops, ordered=False)
    existentes = set(col.distinct("id_cuenta", {"id_cuenta": {"$in": ids}}))
    no_encontradas = sorted(set(ids) - existentes)
    return {
        "actualizadas":     res.modified_count,
        "matched":          res.matched_count,
        "filas_validas":    len(ops),
        "sin_id":           sin_id,
        "sin_campos":       sin_campos,
        "sin_numeros":      sin_numeros,
        "n_no_encontradas": len(no_encontradas),
        "no_encontradas":   no_encontradas[:50],
    }
