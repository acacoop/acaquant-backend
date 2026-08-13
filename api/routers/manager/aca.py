"""Manager sub-router — ACA (gestión de la vista /aca, módulo `manager`).

Tab `/manager → ACA`: lo configurable del RESUMEN EJECUTIVO — la regla de moneda
que decide Total Dolarizado vs Total Pesos, los emisores y clases que las
métricas muestran siempre (aunque den cero) y el catálogo de series del
histórico (columnas de la planilla + líneas de los gráficos).

Entrar a la tab lo da el módulo `manager` (admin). ESCRIBIR acá lo da la misma
allowlist que escribe en la vista (Mesa de Dinero): la configuración cambia
números del informe, así que no puede ser un permiso más flojo que cargar el
informe. Un admin cumple las dos condiciones.

Endpoints (prefix /api/manager lo agrega el paquete):
  GET    /aca/catalogos          → todo lo configurable, en un request
  PUT    /aca/moneda-regla       → 'cartera'|'clase' + clave → 'usd'|'ars'
  DELETE /aca/moneda-regla
  PUT    /aca/emisor             → emisor destacado de un bloque (hd/dl/privados)
  DELETE /aca/emisor
  PUT    /aca/clase              → clase destacada de una cartera
  DELETE /aca/clase
  PUT    /aca/serie              → alta/edición de una serie del histórico
  DELETE /aca/serie              → baja LÓGICA (conserva los valores históricos)
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import aca as _svc

router = APIRouter()


def _ok(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/aca/catalogos")
def catalogos() -> dict:
    return _svc.catalogos()


# ── Regla de moneda (Total Dolarizado / Total Pesos) ─────────────────────────

class _MonedaRegla(BaseModel):
    scope: str = Field(..., description="'cartera' | 'clase'")
    clave: str = Field(..., min_length=1, max_length=128)
    moneda: str = Field(..., description="'usd' | 'ars'")


@router.put("/aca/moneda-regla")
def set_moneda_regla(req: _MonedaRegla = Body(...),
                     actor: str = Depends(get_user_email)) -> dict:
    return _ok(_svc.set_moneda_regla, req.scope, req.clave, req.moneda, actor=actor)


@router.delete("/aca/moneda-regla")
def del_moneda_regla(scope: str = Query(...), clave: str = Query(...),
                     actor: str = Depends(get_user_email)) -> dict:
    return _ok(_svc.del_moneda_regla, scope, clave, actor=actor)


# ── Emisores / clases destacadas de MÉTRICAS GENERALES ──────────────────────

class _Emisor(BaseModel):
    bloque: str = Field(..., description="'hd' | 'dl' | 'privados'")
    emisor: str = Field(..., min_length=1, max_length=256)
    orden: int = 0


@router.put("/aca/emisor")
def set_emisor(req: _Emisor = Body(...), actor: str = Depends(get_user_email)) -> dict:
    return _ok(_svc.set_emisor_destacado, req.bloque, req.emisor, req.orden, actor=actor)


@router.delete("/aca/emisor")
def del_emisor(bloque: str = Query(...), emisor: str = Query(...),
               actor: str = Depends(get_user_email)) -> dict:
    return _ok(_svc.del_emisor_destacado, bloque, emisor, actor=actor)


class _Clase(BaseModel):
    cartera: str = Field(..., min_length=1, max_length=32)
    clase: str = Field(..., min_length=1, max_length=128)
    orden: int = 0


@router.put("/aca/clase")
def set_clase(req: _Clase = Body(...), actor: str = Depends(get_user_email)) -> dict:
    return _ok(_svc.set_clase_destacada, req.cartera, req.clase, req.orden, actor=actor)


@router.delete("/aca/clase")
def del_clase(cartera: str = Query(...), clase: str = Query(...),
              actor: str = Depends(get_user_email)) -> dict:
    return _ok(_svc.del_clase_destacada, cartera, clase, actor=actor)


# ── Series del histórico ────────────────────────────────────────────────────

class _Serie(BaseModel):
    codigo: str = Field(..., min_length=1, max_length=64)
    nombre: str = Field(..., min_length=1, max_length=128)
    grupo: str | None = Field(None, max_length=32)
    fuente: str = Field("manual", description="'manual' | 'macro_var:<SERIE>' | 'macro_pct:<SERIE>'")
    escala: float = 100
    graficos: list[str] = Field(default_factory=list)
    color: str | None = Field(None, max_length=32)
    orden: int = 0
    activo: bool = True


@router.put("/aca/serie")
def set_serie(req: _Serie = Body(...), actor: str = Depends(get_user_email)) -> dict:
    return _ok(_svc.set_serie, req.model_dump(), actor=actor)


@router.delete("/aca/serie")
def del_serie(codigo: str = Query(...), actor: str = Depends(get_user_email)) -> dict:
    return _ok(_svc.del_serie, codigo, actor=actor)
