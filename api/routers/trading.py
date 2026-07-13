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


@router.get("/trades")
def trades(ticker: str, limite: int = 200):
    """Time & Sales (tape) del activo de la card. Resuelve la fuente por
    `ticker` (ticker_corto) igual que /pivots: CEDEAR → tabla de CEDEARs; bono
    → mercado.timesales (tape de renta fija). Shape:
    [{timestamp, price, size, side, money}] desc por ts."""
    return svc.get_trades(ticker=ticker, limite=limite)


@router.get("/intraday")
def intraday(ticker: str):
    """Serie intradía por minuto (OHLC) para el chart LIVE. Resuelve la fuente
    por `ticker` igual que /pivots y /trades: CEDEAR → tabla de CEDEARs; bono →
    mercado.timesales agregado por minuto. Shape: [{t, o, h, l, c, vol}] asc."""
    return svc.get_intraday(ticker=ticker)


@router.get("/pivot-radar")
def pivot_radar():
    """Radar de proximidad a pivote de TODO el universo de CEDEARs. Cada item:
    {ticker, last, nivel, nivel_precio, dist_pct}. Ordenado por |dist_pct| asc;
    el frontend filtra por el umbral elegido."""
    return svc.pivot_radar()


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
