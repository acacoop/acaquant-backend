"""Manager sub-router — gestión de ONs, DIRECTO sobre Trading.Curvas (curva on_<sector>).

Tab `/manager → TÍTULOS → ONs`. Dos cosas:
  - SEGMENTAR: setear el sector de cada ON (energia/finanzas/otros). Live: se
    refleja en la vista /ons sin reiniciar motores (cambia la curva on_<sector>).
  - ALTA/EDICIÓN: crear/editar una ON con sus flujos (cronograma de pagos).

UNA sola base: cada mutación escribe `Trading.Curvas` directo (BondsMaster RETIRADO,
Fase 3 2026-06-22), así el bono aparece en la vista al instante. La lógica pura vive
en `api/services/ons.py`.

Endpoints (gate `manager_titulos`, igual que assets):
  GET    /api/manager/ons              → lista filtrable (sector, emisor)
  GET    /api/manager/ons/values       → valores únicos (emisores/sectores/monedas)
  POST   /api/manager/ons              → alta/edición (upsert por `asset`)
  PATCH  /api/manager/ons/sector       → segmentar (setear sector)
  DELETE /api/manager/ons?asset=X      → baja
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import ons as svc

router = APIRouter()


class _Tickers(BaseModel):
    ARS: str | None = Field(None, max_length=64)
    USD: str | None = Field(None, max_length=64)


class _Flujo(BaseModel):
    fecha: str = Field(..., max_length=10)          # YYYY-MM-DD
    amortizacion: float = 0.0
    interes: float = 0.0
    valor_residual: float = 100.0


class _ONUpsert(BaseModel):
    asset: str = Field(..., min_length=1, max_length=64)
    emisor: str | None = Field(None, max_length=128)
    moneda_flujo: str | None = Field(None, max_length=8)
    tasa_cupon: float | None = None
    vencimiento: str | None = Field(None, max_length=10)
    sector: str | None = Field(None, max_length=64)
    tickers: _Tickers | None = None
    flujos: list[_Flujo] | None = None


class _SectorPatch(BaseModel):
    asset: str = Field(..., min_length=1, max_length=64)
    sector: str = Field(..., min_length=1, max_length=64)


class _ParseFlujos(BaseModel):
    texto: str = Field(..., max_length=100_000)


class _Ignorar(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=64)


@router.get("/ons")
def list_ons(
    sector: str | None = Query(None),
    emisor: str | None = Query(None),
) -> dict:
    ons = svc.list_ons(sector=sector, emisor=emisor)
    return {"ons": ons, "n": len(ons)}


@router.get("/ons/values")
def ons_values() -> dict:
    return svc.ons_values()


@router.post("/ons")
def upsert_on(req: _ONUpsert = Body(...), actor: str = Depends(get_user_email)) -> dict:
    payload = req.model_dump(exclude_none=True)
    try:
        return svc.upsert_on(payload, actor=actor or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # error real en JSON (no 500 opaco) para diagnosticar
        raise HTTPException(status_code=500, detail=f"upsert_on falló: {e}") from e


@router.patch("/ons/sector")
def set_sector(req: _SectorPatch = Body(...), actor: str = Depends(get_user_email)) -> dict:
    try:
        return svc.set_sector(req.asset, req.sector, actor=actor or "")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/ons")
def delete_on(asset: str = Query(..., min_length=1)) -> dict:
    try:
        return svc.delete_on(asset)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/ons/parse-flujos")
def parse_flujos(req: _ParseFlujos = Body(...)) -> dict:
    """Parsea flujos pegados de Excel (formato oficial BYMA/IAMC o simple) →
    {flujos, tasa_cupon, vencimiento, formato} para previsualizar en el form."""
    try:
        return svc.parse_flujos_texto(req.texto)
    except Exception as e:
        return {"flujos": [], "tasa_cupon": None, "vencimiento": None,
                "formato": "error", "error": str(e)}


@router.get("/ons/conciliar")
def conciliar() -> dict:
    """Gap de cobertura: HD/DL que tienen los clientes y faltan en Curvas."""
    return svc.conciliar()


@router.get("/ons/ignoradas")
def ignoradas() -> dict:
    """Lista los tickers ignorados en el conciliador (para verlos / restaurarlos)."""
    return svc.listar_ignoradas()


@router.post("/ons/ignorar")
def ignorar(req: _Ignorar = Body(...), actor: str = Depends(get_user_email)) -> dict:
    try:
        return svc.ignorar_concil(req.ticker, actor=actor or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/ons/ignorar")
def quitar_ignorar(ticker: str = Query(..., min_length=1)) -> dict:
    return svc.quitar_ignorar(ticker)
