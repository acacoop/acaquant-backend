"""Manager sub-router — Títulos → Bonos (Trading.Curvas directo, NO-ON).

Gemelo de `ons.py` pero para bonos soberanos / tasa_fija (Lecaps/Boncaps) / CER,
que viven DIRECTO en Trading.Curvas (sin BondsMaster). Comparte el parser de
flujos de ONs (`POST /api/manager/ons/parse-flujos`). Gate `manager_titulos`.

  GET    /api/manager/bonos                 → lista (curva opcional)
  POST   /api/manager/bonos                 → alta/edición (upsert por ticker_corto)
  DELETE /api/manager/bonos?ticker_corto=X  → baja
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import bonos_admin as svc

router = APIRouter()


class _Flujo(BaseModel):
    fecha: str = Field(..., max_length=10)
    amortizacion: float = 0.0
    interes: float = 0.0
    valor_residual: float = 100.0


class _BonoUpsert(BaseModel):
    ticker_corto: str = Field(..., min_length=1, max_length=32)
    ticker: str = Field(..., min_length=1, max_length=64)
    curva: str = Field(..., min_length=1, max_length=32)
    tipo: str | None = Field(None, max_length=32)
    moneda_flujo: str | None = Field(None, max_length=8)
    fecha_vencimiento: str | None = Field(None, max_length=10)
    valor_nominal: float | None = None
    cer_emision: float | None = None
    flujo_vencimiento: float | None = None     # bullet (Lecap/Boncap)
    flujos: list[_Flujo] | None = None          # cronograma (cupón)


@router.get("/bonos")
def list_bonos(curva: str | None = Query(None)) -> dict:
    bonos = svc.list_bonos(curva=curva)
    return {"bonos": bonos, "n": len(bonos)}


@router.get("/bonos/sin-flujo")
def bonos_sin_flujo() -> dict:
    """Conciliador unificado: bonos cartera ARS/DL/HD faltantes o incompletos en
    Trading.Curvas (no-ON) o BondsMaster (ONs), con la acción para resolver cada uno.
    Gate `manager_titulos`. Ignorar/designorar: usar /api/manager/ons/ignorar."""
    from api.services.acreencias import titulos_sin_flujo
    falta = titulos_sin_flujo()
    return {
        "total": len(falta),
        "en_cartera": sum(1 for t in falta if t["en_cartera"]),
        "por_fuente": {
            "curvas": sum(1 for t in falta if t["fuente"] == "curvas"),
            "bondsmaster": sum(1 for t in falta if t["fuente"] == "bondsmaster"),
            "ninguna": sum(1 for t in falta if t["fuente"] == "ninguna"),
        },
        "ok": not falta,
        "titulos": falta,
    }


@router.post("/bonos")
def upsert_bono(req: _BonoUpsert = Body(...), actor: str = Depends(get_user_email)) -> dict:
    try:
        return svc.upsert_bono(req.model_dump(exclude_none=True), actor=actor or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"upsert_bono falló: {e}") from e


@router.delete("/bonos")
def delete_bono(ticker_corto: str = Query(..., min_length=1)) -> dict:
    try:
        return svc.delete_bono(ticker_corto)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
