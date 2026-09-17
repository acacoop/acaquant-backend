"""Manager sub-router — Títulos → Renta Variable (CEDEARs: rubro + es_ia + ric).

Editor del catálogo de clasificación de CEDEARs (rubro de negocio + flag ecosistema IA
+ RIC Refinitiv del subyacente). Análogo a la segmentación de clientes: el `rubro` NO se
escribe libre — se elige del catálogo `mercado.rubros` o se crea con POST /rubro. El `ric`
sí es texto libre (ej. 'AAPL.O'); lo usan RESEARCH (fundamentals) y el feed live de Eikon
(`scripts/eikon_feed_simple.py`). Lógica en `api/services/renta_variable_admin_sql.py`.
Gate `manager_titulos`.

  GET    /api/manager/renta-variable          → grid de CEDEARs (ticker, nombre, rubro, es_ia, ric)
  GET    /api/manager/renta-variable/rubros   → catálogo de rubros (dropdown)
  POST   /api/manager/renta-variable/rubro    → crear un rubro nuevo
  PATCH  /api/manager/renta-variable          → setear rubro/es_ia/ric de un CEDEAR (ticker en body)
  DELETE /api/manager/renta-variable?ticker=  → sacar un CEDEAR del universo
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from api.services import renta_variable_admin_sql as svc

router = APIRouter()


@router.get("/renta-variable")
def listar_renta_variable() -> list[dict]:
    """Todos los CEDEARs con su clasificación."""
    return svc.listar_cedears()


@router.get("/renta-variable/rubros")
def listar_rubros() -> list[dict]:
    """Catálogo controlado de rubros (para el dropdown del editor)."""
    return svc.listar_rubros()


class _RubroNuevo(BaseModel):
    rubro: str = Field(..., min_length=1, max_length=80)
    es_ia_def: bool = False


@router.post("/renta-variable/rubro")
def crear_rubro(req: _RubroNuevo = Body(...)) -> dict:
    """Crea un rubro nuevo en el catálogo (idempotente — si existe, no rompe)."""
    rub = req.rubro.strip()
    if not rub:
        raise HTTPException(400, "rubro vacío")
    svc.crear_rubro(rub, req.es_ia_def)
    return {"ok": True, "rubro": rub}


class _CedearPatch(BaseModel):
    ticker: str = Field(..., max_length=60)     # ticker BYMA completo (PK de mercado.cedears)
    rubro: str | None = None                    # debe existir en mercado.rubros (None = vaciar)
    es_ia: bool | None = None
    ric: str | None = Field(default=None, max_length=40)   # RIC Refinitiv (None/'' = vaciar)
    ratio: float | None = Field(default=None, gt=0)        # CEDEARs por acción (None = vaciar)
    # Prender/apagar. Es lo que REACTIVA un CEDEAR que el agente descartó
    # («no me interesan», `cedears_sql.descartar`, AGENT.md §0.el): el descarte
    # es `activo = false` en esta misma tabla, y la vuelta es este campo.
    activo: bool | None = None


@router.patch("/renta-variable")
def patch_cedear(req: _CedearPatch = Body(...)) -> dict:
    """Setea rubro/es_ia/ric de un CEDEAR. El rubro NO se escribe libre: tiene que estar en el
    catálogo (si no, 400 → crearlo primero con POST /rubro). El ric es texto libre."""
    sets: dict = {}
    if "rubro" in req.model_fields_set:
        rub = (req.rubro or "").strip() or None
        if rub is not None and not svc.rubro_existe(rub):
            raise HTTPException(
                400, f"rubro inexistente: {rub!r} — crealo primero con POST /rubro")
        sets["rubro"] = rub
    if "es_ia" in req.model_fields_set:
        sets["es_ia"] = req.es_ia
    if "ric" in req.model_fields_set:
        sets["ric"] = (req.ric or "").strip() or None
    if "ratio" in req.model_fields_set:
        sets["ratio"] = req.ratio
    if "activo" in req.model_fields_set and req.activo is not None:
        sets["activo"] = req.activo
    if not sets:
        raise HTTPException(400, "body sin campos editables (rubro / es_ia / ric / ratio / activo)")
    if svc.actualizar_cedear(req.ticker, sets) == 0:
        raise HTTPException(404, f"ticker no encontrado: {req.ticker!r}")
    return {"ok": True, "ticker": req.ticker, **sets}


@router.delete("/renta-variable")
def borrar_cedear(ticker: str = Query(..., description="ticker BYMA completo (PK)")) -> dict:
    """Saca un CEDEAR del universo para dejar de suscribirlo. Borra del MASTER SQL
    `mercado.cedears` (fuente de verdad del motor + scanner) y de su snapshot. El motor
    deja de trackearlo en el próximo restart; `precios_acciones_daily`/`adr_live` dejan
    de pedir su underlying.
    Reversible solo re-dándolo de alta (scripts/add_cedear)."""
    sql_del = svc.borrar_cedear(ticker)
    if not sql_del:
        raise HTTPException(404, f"ticker no encontrado: {ticker!r}")
    return {"ok": True, "ticker": ticker, "sql_borrado": sql_del}
