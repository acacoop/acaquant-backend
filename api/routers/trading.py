"""Router /api/trading — vista TRADING (módulo `trading`, admin-only).

Por ahora: pivots Floor Trader sobre el CEDEAR (ARS) + el catálogo de CEDEARs
para el selector de instrumento de las cards. Lógica pura en
api/services/trading_pivots.py. Ver [[project_vista_trading]].
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from api.services import scanner_sql as scanner_svc
from api.services import trading_pivots as svc

router = APIRouter(prefix="/api/trading", tags=["Trading"])


@router.get("/pivots")
def pivots(tickers: str = Query("", description="CSV de ticker_corto de CEDEARs")):
    """Pivots del CEDEAR por ticker. Cada item: {ticker, last, fecha, high, low,
    close, pivots:{pp,r1,r2,r3,s1,s2,s3}} o {ticker, last, sin_datos:true}."""
    lista = [t.strip() for t in tickers.split(",") if t.strip()]
    return svc.get_pivots(tickers=lista)


@router.get("/universo")
def universo():
    """Catálogo liviano para el selector: CEDEARs activos + bonos de renta fija.
    Cada item: {ticker_corto, nombre, clase: 'cedear'|'bono'}. El endpoint /pivots
    resuelve la fuente de datos por `ticker_corto` (no necesita `clase`)."""
    cedears = [
        {"ticker_corto": r.get("ticker_corto"), "nombre": r.get("nombre") or "",
         "clase": "cedear"}
        for r in scanner_svc.get_universo()
    ]
    return cedears + svc.bonos_universo()
