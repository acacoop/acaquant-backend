"""Manager sub-router — control de Valuaciones.Assets.

Tab `/manager → ASSETS` para auditar y completar metadatos faltantes
(CARTERA, EMISOR, INSTRUMENTO, etc.) en Valuaciones.Assets, que es
la fuente de verdad UPPERCASE — el resto del sistema (TitulosAPI.AssetsAPI)
se deriva de acá vía `scripts/api_migrate.py:assets`.

Endpoints:
  GET   /api/manager/assets/gaps      → assets con CARTERA o EMISOR vacíos
                                        / "NO APLICA" / null
  PATCH /api/manager/assets/{unidad}  → edita campos UPPERCASE
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_user_email
from core.mongo import get_mongo_client, get_mongo_client_read

router = APIRouter()

# Valores que tratamos como "vacío" para detectar gaps en metadata.
_EMPTY_VALUES: list[str | None] = ["", "NO APLICA", None]

# Campos UPPERCASE editables. Espejan el shape del doc en Valuaciones.Assets.
_EDITABLE_FIELDS: tuple[str, ...] = (
    "CARTERA", "EMISOR", "INSTRUMENTO",
    "CLASE_ACTIVO", "CALIFICACION", "TICKER", "VENCIMIENTO",
)


@router.get("/assets/gaps")
def get_assets_gaps() -> dict:
    """Lista assets en `Valuaciones.Assets` donde CARTERA o EMISOR están
    vacíos / "NO APLICA" / null. Útil para que el manager complete metadata
    faltante una unidad por vez.

    Returns:
        {
          assets: [{unidad, CARTERA, EMISOR, INSTRUMENTO, CLASE_ACTIVO,
                    CALIFICACION, TICKER, VENCIMIENTO,
                    actualizado_por, actualizado_at}, ...],
          n: int
        }
    """
    col = get_mongo_client_read()["Valuaciones"]["Assets"]
    cur = col.find(
        {"$or": [
            {"CARTERA": {"$in": _EMPTY_VALUES}},
            {"EMISOR":  {"$in": _EMPTY_VALUES}},
        ]},
        {"_id": 0, "unidad": 1,
         "CARTERA": 1, "EMISOR": 1, "INSTRUMENTO": 1, "CLASE_ACTIVO": 1,
         "CALIFICACION": 1, "TICKER": 1, "VENCIMIENTO": 1,
         "actualizado_por": 1, "actualizado_at": 1},
    ).sort("unidad", 1)
    assets = list(cur)
    # Normalizar `actualizado_at` a ISO string si viene como datetime.
    for a in assets:
        ts = a.get("actualizado_at")
        if isinstance(ts, datetime):
            a["actualizado_at"] = ts.replace(tzinfo=UTC).isoformat() if ts.tzinfo is None else ts.isoformat()
    return {"assets": assets, "n": len(assets)}


class _AssetPatch(BaseModel):
    """Todos los campos opcionales — solo se actualizan los que vengan en el body."""
    CARTERA:      str | None = Field(None, max_length=128)
    EMISOR:       str | None = Field(None, max_length=128)
    INSTRUMENTO:  str | None = Field(None, max_length=256)
    CLASE_ACTIVO: str | None = Field(None, max_length=128)
    CALIFICACION: str | None = Field(None, max_length=128)
    TICKER:       str | None = Field(None, max_length=64)
    VENCIMIENTO:  str | None = Field(None, max_length=64)


@router.patch("/assets/{unidad}")
def patch_asset(
    unidad: str,
    req: _AssetPatch = Body(...),
    actor: str = Depends(get_user_email),
):
    """Update parcial de campos UPPERCASE. Setea `actualizado_por` y
    `actualizado_at` para audit liviano.

    Body: subset de {CARTERA, EMISOR, INSTRUMENTO, CLASE_ACTIVO,
                     CALIFICACION, TICKER, VENCIMIENTO}.
    """
    payload = req.model_dump(exclude_none=True)
    if not payload:
        raise HTTPException(400, "body vacío — pasá al menos un campo a actualizar")

    # Whitelist defensivo (Pydantic ya filtra, pero por las dudas).
    set_fields = {k: v for k, v in payload.items() if k in _EDITABLE_FIELDS}
    if not set_fields:
        raise HTTPException(400, "ningún campo válido en el body")

    set_fields["actualizado_por"] = actor
    set_fields["actualizado_at"]  = datetime.now(UTC)

    col = get_mongo_client()["Valuaciones"]["Assets"]
    result = col.update_one({"unidad": unidad}, {"$set": set_fields})
    if result.matched_count == 0:
        raise HTTPException(404, f"unidad no encontrada: {unidad!r}")

    # Devolvemos el doc actualizado para que el frontend refresque la fila.
    doc = col.find_one(
        {"unidad": unidad},
        {"_id": 0, "unidad": 1,
         "CARTERA": 1, "EMISOR": 1, "INSTRUMENTO": 1, "CLASE_ACTIVO": 1,
         "CALIFICACION": 1, "TICKER": 1, "VENCIMIENTO": 1,
         "actualizado_por": 1, "actualizado_at": 1},
    ) or {}
    ts = doc.get("actualizado_at")
    if isinstance(ts, datetime):
        doc["actualizado_at"] = ts.replace(tzinfo=UTC).isoformat() if ts.tzinfo is None else ts.isoformat()
    return doc
