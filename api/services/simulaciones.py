"""Capa de servicio — simulaciones de cartera por usuario.

Cada usuario puede crear, guardar, editar y borrar carteras hipotéticas
(posiciones = lista de `{ticker, importe}`). El cálculo de cashflows /
métricas / composición se agrega en una segunda capa (ver función
`calcular`, próximamente PR2).

Persistencia en `Trading.Simulaciones`. Filtro por `user_email` en cada
query — un usuario nunca puede leer ni mutar simulaciones de otro.

Índice recomendado (manual en Mongo):
    db.Simulaciones.createIndex({user_email: 1, _id: 1})
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pydantic import BaseModel, Field
from pymongo import ReturnDocument

from core.mongo import get_mongo_client

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────


class Posicion(BaseModel):
    """Una línea de la cartera: ticker + importe en moneda del activo.

    El `importe` se interpreta en la moneda del instrumento (ARS para CER /
    tasa fija / TAMAR; USD para globales / bonares). El service de cálculo
    (PR2) deriva la moneda del `clase_activo` y proyecta cashflows en
    consecuencia.
    """

    ticker: str = Field(..., min_length=1, max_length=50)
    importe: float = Field(..., gt=0)


class SimulacionCreate(BaseModel):
    """Body de POST /api/simulaciones."""

    nombre: str = Field(..., min_length=1, max_length=200)
    posiciones: list[Posicion] = Field(default_factory=list)


class SimulacionUpdate(BaseModel):
    """Body de PUT /api/simulaciones/{id}. Campos opcionales — solo se
    actualizan los que vienen distintos de None (PATCH semantics)."""

    nombre: str | None = Field(default=None, min_length=1, max_length=200)
    posiciones: list[Posicion] | None = None


class Simulacion(BaseModel):
    """Doc completo serializado a la API. `id` es el ObjectId como string."""

    id: str
    user_email: str
    nombre: str
    posiciones: list[Posicion]
    creado: datetime
    actualizado: datetime


# ─────────────────────────────────────────────────────────────────────────────
# Acceso a Mongo
# ─────────────────────────────────────────────────────────────────────────────


def _coll():
    """Colección rw — escribimos y leemos por la misma para evitar lag
    de replicación (el usuario crea y quiere ver el doc al instante)."""
    return get_mongo_client()["Trading"]["Simulaciones"]


def _doc_to_model(doc: dict[str, Any]) -> Simulacion:
    """Mongo doc → Simulacion. Mapea _id (ObjectId) → id (str)."""
    return Simulacion(
        id=str(doc["_id"]),
        user_email=doc["user_email"],
        nombre=doc["nombre"],
        posiciones=[Posicion(**p) for p in doc.get("posiciones", [])],
        creado=doc["creado"],
        actualizado=doc["actualizado"],
    )


def _parse_object_id(simulacion_id: str) -> ObjectId | None:
    """ObjectId() tira si el string no tiene 24 hex chars. Lo capturamos
    y devolvemos None — el caller responde 404 en lugar de 500."""
    try:
        return ObjectId(simulacion_id)
    except (InvalidId, TypeError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────────────────────────────


def listar_simulaciones(user_email: str) -> list[Simulacion]:
    """Todas las simulaciones del usuario, más recientes primero."""
    docs = _coll().find({"user_email": user_email}).sort("actualizado", -1)
    return [_doc_to_model(d) for d in docs]


def crear_simulacion(user_email: str, data: SimulacionCreate) -> Simulacion:
    """Crea una nueva simulación. `creado` y `actualizado` se setean al ahora."""
    ahora = datetime.now(UTC)
    doc = {
        "user_email": user_email,
        "nombre": data.nombre,
        "posiciones": [p.model_dump() for p in data.posiciones],
        "creado": ahora,
        "actualizado": ahora,
    }
    res = _coll().insert_one(doc)
    doc["_id"] = res.inserted_id
    return _doc_to_model(doc)


def obtener_simulacion(simulacion_id: str, user_email: str) -> Simulacion | None:
    """Devuelve None si el id es inválido, no existe, o pertenece a otro usuario."""
    oid = _parse_object_id(simulacion_id)
    if oid is None:
        return None
    doc = _coll().find_one({"_id": oid, "user_email": user_email})
    return _doc_to_model(doc) if doc else None


def actualizar_simulacion(
    simulacion_id: str,
    user_email: str,
    data: SimulacionUpdate,
) -> Simulacion | None:
    """Actualiza nombre y/o posiciones. Devuelve None si no existe o es de otro
    usuario. Si `data` no trae cambios, devuelve el doc actual sin tocar Mongo."""
    oid = _parse_object_id(simulacion_id)
    if oid is None:
        return None

    update_fields: dict[str, Any] = {}
    if data.nombre is not None:
        update_fields["nombre"] = data.nombre
    if data.posiciones is not None:
        update_fields["posiciones"] = [p.model_dump() for p in data.posiciones]

    if not update_fields:
        # PATCH vacío — devolvemos el doc actual sin escribir.
        doc = _coll().find_one({"_id": oid, "user_email": user_email})
        return _doc_to_model(doc) if doc else None

    update_fields["actualizado"] = datetime.now(UTC)
    doc = _coll().find_one_and_update(
        {"_id": oid, "user_email": user_email},
        {"$set": update_fields},
        return_document=ReturnDocument.AFTER,
    )
    return _doc_to_model(doc) if doc else None


def eliminar_simulacion(simulacion_id: str, user_email: str) -> bool:
    """True si se eliminó algo, False si no existía o era de otro usuario."""
    oid = _parse_object_id(simulacion_id)
    if oid is None:
        return False
    res = _coll().delete_one({"_id": oid, "user_email": user_email})
    return res.deleted_count > 0
