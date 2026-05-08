"""Manager sub-router — control de Valuaciones.Assets.

Tab `/manager → ASSETS` para auditar y completar metadatos faltantes
(CARTERA, EMISOR, INSTRUMENTO, etc.) en Valuaciones.Assets, que es
la fuente de verdad UPPERCASE — el resto del sistema (TitulosAPI.AssetsAPI)
se deriva de acá vía `scripts/api_migrate.py:assets`.

Endpoints:
  GET   /api/manager/assets             → lista filtrable (cartera, emisor,
                                          solo_gaps). Default = solo gaps.
  GET   /api/manager/assets/gaps        → alias de GET /assets (compat).
  GET   /api/manager/assets/values      → valores únicos para autocomplete.
  PATCH /api/manager/assets             → edita campos UPPERCASE. unidad
                                          va en el body (no path) para evitar
                                          problemas de URL-encoding con
                                          caracteres especiales.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query
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

_PROJECTION = {
    "_id": 0, "unidad": 1,
    "CARTERA": 1, "EMISOR": 1, "INSTRUMENTO": 1, "CLASE_ACTIVO": 1,
    "CALIFICACION": 1, "TICKER": 1, "VENCIMIENTO": 1,
    # CAFCI es read-only — derivado de `unidad` por jobs/aum.py.
    # No está en _EDITABLE_FIELDS adrede.
    "CAFCI": 1,
    "actualizado_por": 1, "actualizado_at": 1,
}


def _normalize_assets(assets: list[dict]) -> list[dict]:
    """Convierte `actualizado_at` (datetime) a ISO string."""
    for a in assets:
        ts = a.get("actualizado_at")
        if isinstance(ts, datetime):
            tz_aware = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
            a["actualizado_at"] = tz_aware.isoformat()
    return assets


def _list_assets(
    cartera: str | None = None,
    emisor: str | None = None,
    solo_gaps: bool = True,
) -> list[dict]:
    """Query base reutilizada por GET /assets y GET /assets/gaps."""
    col = get_mongo_client_read()["Valuaciones"]["Assets"]
    filtros: list[dict] = []

    if cartera:
        filtros.append({"CARTERA": cartera})
    if emisor:
        filtros.append({"EMISOR": emisor})
    if solo_gaps:
        # Mismo criterio que antes: CARTERA o EMISOR vacío / "NO APLICA" / null.
        filtros.append({"$or": [
            {"CARTERA": {"$in": _EMPTY_VALUES}},
            {"EMISOR":  {"$in": _EMPTY_VALUES}},
        ]})

    query: dict = {"$and": filtros} if filtros else {}
    cur = col.find(query, _PROJECTION).sort("unidad", 1).limit(2000)
    return _normalize_assets(list(cur))


@router.get("/assets")
def list_assets(
    cartera:   str | None = Query(None, description="Filtrar por CARTERA exacta"),
    emisor:    str | None = Query(None, description="Filtrar por EMISOR exacto"),
    solo_gaps: bool = Query(
        True,
        description="Si True (default), solo devuelve assets con CARTERA "
                    "o EMISOR vacíos / 'NO APLICA' / null.",
    ),
) -> dict:
    """Lista assets de Valuaciones.Assets con filtros opcionales."""
    assets = _list_assets(cartera=cartera, emisor=emisor, solo_gaps=solo_gaps)
    return {"assets": assets, "n": len(assets)}


@router.get("/assets/gaps")
def get_assets_gaps() -> dict:
    """Alias de GET /assets con solo_gaps=True (compat con clientes viejos)."""
    assets = _list_assets(solo_gaps=True)
    return {"assets": assets, "n": len(assets)}


@router.get("/assets/values")
def get_assets_values() -> dict:
    """Valores únicos de CARTERA y EMISOR para autocomplete del input
    en el form. Filtra cadenas vacías y "NO APLICA" — no tiene sentido
    sugerir un valor que es lo que el usuario está intentando reemplazar.

    Returns:
        {carteras: [<sorted unique strings>], emisores: [<...>]}
    """
    col = get_mongo_client_read()["Valuaciones"]["Assets"]
    placeholders = {"", "NO APLICA"}

    raw_cart = col.distinct("CARTERA")
    raw_emi = col.distinct("EMISOR")

    carteras = sorted({c for c in raw_cart if c and c not in placeholders})
    emisores = sorted({e for e in raw_emi if e and e not in placeholders})
    return {"carteras": carteras, "emisores": emisores}


class _AssetPatch(BaseModel):
    """unidad va en el body — antes era path-param y rompía con caracteres
    especiales (corchetes, espacios, slashes) tras URL-encoding.
    El resto son opcionales — solo se actualizan los que vengan."""
    unidad:       str = Field(..., min_length=1, max_length=512)
    CARTERA:      str | None = Field(None, max_length=128)
    EMISOR:       str | None = Field(None, max_length=128)
    INSTRUMENTO:  str | None = Field(None, max_length=256)
    CLASE_ACTIVO: str | None = Field(None, max_length=128)
    CALIFICACION: str | None = Field(None, max_length=128)
    TICKER:       str | None = Field(None, max_length=64)
    VENCIMIENTO:  str | None = Field(None, max_length=64)


@router.patch("/assets")
def patch_asset(
    req: _AssetPatch = Body(...),
    actor: str = Depends(get_user_email),
):
    """Update parcial de campos UPPERCASE. Setea `actualizado_por` y
    `actualizado_at` para audit liviano. `unidad` va en el body."""
    payload = req.model_dump(exclude_none=True)
    unidad = payload.pop("unidad")

    set_fields = {k: v for k, v in payload.items() if k in _EDITABLE_FIELDS}
    if not set_fields:
        raise HTTPException(400, "body sin campos editables — pasá al "
                                  "menos uno de CARTERA, EMISOR, INSTRUMENTO, "
                                  "CLASE_ACTIVO, CALIFICACION, TICKER, VENCIMIENTO.")

    set_fields["actualizado_por"] = actor
    set_fields["actualizado_at"]  = datetime.now(UTC)

    col = get_mongo_client()["Valuaciones"]["Assets"]
    result = col.update_one({"unidad": unidad}, {"$set": set_fields})
    if result.matched_count == 0:
        raise HTTPException(404, f"unidad no encontrada en Valuaciones.Assets: {unidad!r}")

    doc = col.find_one({"unidad": unidad}, _PROJECTION) or {}
    return _normalize_assets([doc])[0]


# Compat: PATCH /assets/{unidad} sigue funcionando para clientes viejos
# pero internamente delega al nuevo handler. Usar el body es preferible.
@router.patch("/assets/{unidad}")
def patch_asset_legacy(
    unidad: str,
    body: dict = Body(...),
    actor: str = Depends(get_user_email),
):
    """DEPRECATED — use PATCH /api/manager/assets con unidad en body."""
    body["unidad"] = unidad
    req = _AssetPatch(**body)
    return patch_asset(req=req, actor=actor)
