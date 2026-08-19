"""Manager sub-router — control del catálogo de títulos (segmentación).

Tab `/manager → ASSETS` para auditar y completar metadatos faltantes
(CARTERA, EMISOR, INSTRUMENTO, etc.). LEE y ESCRIBE SQL `portafolio.assets` (la
única fuente de verdad de la segmentación) vía `api/services/assets_sql.py`.
Mongo `Valuaciones.Assets` quedó deprecado.

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
from api.services.assets_sql import (
    asset_one_panel,
    gaps_assets_panel,
    list_assets_panel,
    values_assets_panel,
)

router = APIRouter()

# Campos UPPERCASE editables (string). Espejan el shape del doc en
# portafolio.assets. También son los campos válidos para el filtro `campo_vacio`.
_EDITABLE_FIELDS: tuple[str, ...] = (
    # INSTRUMENTO / INSTRUMENTO_USD: los DOS símbolos de Primary del papel (pata
    # en pesos y pata en dólares). Los deriva el job `assets_autofill` desde
    # `mercado.especies` — el único catálogo de símbolos — relacionando por
    # TICKER. Siguen siendo editables porque el job NUNCA pisa lo cargado: la
    # mesa manda cuando el catálogo de Primary está desactualizado.
    "CARTERA", "EMISOR", "INSTRUMENTO", "INSTRUMENTO_USD",
    "CLASE_ACTIVO", "CALIFICACION", "TICKER", "VENCIMIENTO",
    # CODIGO_CNV: código CNV del instrumento (string; puede tener ceros a la
    # izquierda). Se edita a mano desde el manager.
    "CODIGO_CNV",
)

# Campos editables que NO entran al autocomplete /values: códigos únicos por
# asset → un datalist con miles de valores no aporta nada.
_NO_AUTOCOMPLETE: frozenset[str] = frozenset({"CODIGO_CNV"})

# Campos NUMÉRICOS editables (no entran al autocomplete /values, que es string).
# FEE_ADMIN: honorario de administración del FCI que cobra la sociedad gerente.
# Se guarda como FRACCIÓN decimal (0.01 = 1%) → la comisión sale directa
# (saldo × FEE_ADMIN). Solo tiene sentido para fondos CARTERA=FCI. Lo edita la
# mesa a mano desde Manager → TÍTULOS · FCI; lo consume la vista REFERIDOS.
_EDITABLE_NUM_FIELDS: tuple[str, ...] = ("FEE_ADMIN",)

# Campos BOOLEANOS editables. VIGENTE: si el título sigue existiendo en el
# mercado. Lo apaga solo `jobs/validar_instrumentos` cuando la fecha de
# vencimiento pasó, pero la mesa manda para lo que la fecha no dice (un rescate
# anticipado, un papel sin vencimiento cargado). Por eso una edición manual
# SELLA `vigencia_motivo='manual'`: es la marca que le dice al job que no opine
# más sobre esa fila. Sin ese sello el job la volvería a dar vuelta esa noche.
_EDITABLE_BOOL_FIELDS: tuple[str, ...] = ("VIGENTE",)
_MOTIVO_MANUAL = "manual"


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
    campo_vacio: str | None = None,
) -> list[dict]:
    """Query base desde SQL `portafolio.assets`. Sin filtros devuelve TODO el
    catálogo. `campo_vacio` (campo UPPERCASE): filtra los assets con ese campo
    vacío / 'NO APLICA' / null."""
    return _normalize_assets(list_assets_panel(cartera=cartera, emisor=emisor,
                                               campo_vacio=campo_vacio))


@router.get("/assets")
def list_assets(
    cartera:     str | None = Query(None, description="Filtrar por CARTERA exacta"),
    emisor:      str | None = Query(None, description="Filtrar por EMISOR exacto"),
    campo_vacio: str | None = Query(
        None,
        description="Filtra assets con ese campo UPPERCASE vacío / 'NO "
                    "APLICA' / null. Uno de: CARTERA, EMISOR, INSTRUMENTO, "
                    "CLASE_ACTIVO, CALIFICACION, TICKER, VENCIMIENTO. "
                    "Sin este parámetro devuelve todos los assets.",
    ),
) -> dict:
    """Lista assets de Valuaciones.Assets. Sin filtros: todo el catálogo."""
    assets = _list_assets(cartera=cartera, emisor=emisor, campo_vacio=campo_vacio)
    return {"assets": assets, "n": len(assets)}


@router.get("/assets/gaps")
def get_assets_gaps() -> dict:
    """Compat: assets con CARTERA o EMISOR vacíos (desde SQL portafolio.assets)."""
    assets = _normalize_assets(gaps_assets_panel())
    return {"assets": assets, "n": len(assets)}


@router.get("/assets/values")
def get_assets_values() -> dict:
    """Valores únicos por campo UPPERCASE editable para los inputs del form.
    Filtra cadenas vacías y "NO APLICA" — no tiene sentido sugerir un valor
    que es lo que el usuario está intentando reemplazar.

    El frontend usa `values[CARTERA]` y `values[CLASE_ACTIVO]` como dropdown
    cerrado (no se ingresan valores nuevos) y el resto como datalist editable.

    Returns:
        {
          values: {CARTERA: [...], EMISOR: [...], INSTRUMENTO: [...], ...},
          carteras: [...], emisores: [...],   # alias compat de los filtros
        }
    """
    campos = [c for c in _EDITABLE_FIELDS if c not in _NO_AUTOCOMPLETE]
    values = values_assets_panel(campos)   # SQL portafolio.assets

    return {
        "values": values,
        # Alias compat — los selects de filtro de arriba los consumen directo.
        "carteras": values.get("CARTERA", []),
        "emisores": values.get("EMISOR", []),
    }


class _AssetPatch(BaseModel):
    """unidad va en el body — antes era path-param y rompía con caracteres
    especiales (corchetes, espacios, slashes) tras URL-encoding.
    El resto son opcionales — solo se actualizan los que vengan."""
    unidad:       str = Field(..., min_length=1, max_length=512)
    CARTERA:      str | None = Field(None, max_length=128)
    EMISOR:       str | None = Field(None, max_length=128)
    INSTRUMENTO:  str | None = Field(None, max_length=256)
    INSTRUMENTO_USD: str | None = Field(None, max_length=256)
    CLASE_ACTIVO: str | None = Field(None, max_length=128)
    CALIFICACION: str | None = Field(None, max_length=128)
    TICKER:       str | None = Field(None, max_length=64)
    VENCIMIENTO:  str | None = Field(None, max_length=64)
    CODIGO_CNV:   str | None = Field(None, max_length=64)
    VIGENTE:      bool | None = None
    # Fracción decimal: 0.01 = 1%. Cap 0<fee≤1 (100%) para atajar el error
    # típico de cargar "1" pensando en 1% (sería 100%). El front muestra el %
    # equivalente al lado del input para que se vea a simple vista.
    FEE_ADMIN:    float | None = Field(None, ge=0, le=1)


@router.patch("/assets")
def patch_asset(
    req: _AssetPatch = Body(...),
    actor: str = Depends(get_user_email),
):
    """Update parcial de campos UPPERCASE. Setea `actualizado_por` y
    `actualizado_at` para audit liviano. `unidad` va en el body."""
    payload = req.model_dump(exclude_none=True)
    unidad = payload.pop("unidad")

    set_fields = {k: v for k, v in payload.items()
                  if k in _EDITABLE_FIELDS or k in _EDITABLE_NUM_FIELDS
                  or k in _EDITABLE_BOOL_FIELDS}
    if not set_fields:
        raise HTTPException(400, "body sin campos editables — pasá al "
                                  "menos uno de CARTERA, EMISOR, INSTRUMENTO, "
                                  "CLASE_ACTIVO, CALIFICACION, TICKER, "
                                  "VENCIMIENTO, FEE_ADMIN, CODIGO_CNV.")

    if "VIGENTE" in set_fields:
        # El sello que frena al job (ver _EDITABLE_BOOL_FIELDS).
        set_fields["vigencia_motivo"] = _MOTIVO_MANUAL
        set_fields["vigencia_at"] = datetime.now(UTC)

    set_fields["actualizado_por"] = actor
    set_fields["actualizado_at"]  = datetime.now(UTC)

    # SQL `portafolio.assets` es la ÚNICA fuente: chequeo existencia y escribo ahí.
    if asset_one_panel(unidad) is None:
        raise HTTPException(404, f"unidad no encontrada en portafolio.assets: {unidad!r}")
    _write_sql(unidad, set_fields)

    # Cambiar la CARTERA reclasifica HD/ARS en Tenencia Valorizada (cruce vivo por unidad),
    # pero la tabla AuM-por-día está cacheada (tenencia_dias, ttl 300s) → invalidar para que
    # el cambio se vea al instante en vez de esperar 5 min. (Las posiciones ya son live.)
    if "CARTERA" in set_fields:
        from api.cache import invalidate
        invalidate("tenencia_dias")

    doc = asset_one_panel(unidad) or {}
    return _normalize_assets([doc])[0]


def _write_sql(unidad: str, set_fields: dict) -> None:
    """Delega en `assets_sql.set_campos` — **la única puerta de escritura**.

    El cuerpo vivía acá, en un router. Mientras el único escritor era este panel
    daba igual; desde que el AV Agent también propone cambios de asset, tener la
    escritura en HTTP plumbing obligaba a un service a importar de un router.
    Ahora la puerta está en el service y esto es solo el nombre viejo."""
    from api.services import assets_sql
    assets_sql.set_campos(unidad, set_fields)


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
