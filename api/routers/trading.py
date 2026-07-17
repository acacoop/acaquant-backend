"""Router /api/trading — vista TRADING (módulo `trading`, admin-only).

Por ahora: pivots Floor Trader sobre el CEDEAR (ARS) + el catálogo de CEDEARs
para el selector de instrumento de las cards. Lógica pura en
api/services/trading_pivots.py. Ver [[project_vista_trading]].
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from api.services import scanner_sql as scanner_svc
from api.services import trading_pivots as svc
from core.eikon_live import ficha as ficha_reuters
from core.eikon_live import tablero_fundamentals, tablero_reuters

router = APIRouter(prefix="/api/trading", tags=["Trading"])


@router.get("/reuters/ficha")
def reuters_ficha(ticker: str):
    """FICHA de empresa del tab REUTERS: quote live + fundamentals curados +
    ratio del CEDEAR + velas diarias de 1 año (chart). 404 si el ticker no
    existe en ninguna fuente."""
    out = ficha_reuters(ticker)
    if out is None:
        raise HTTPException(404, f"sin datos para {ticker!r}")
    return out


@router.get("/reuters/fundamentals")
def reuters_fundamentals():
    """Screener FUNDAMENTALS del tab REUTERS: una empresa por fila con las
    métricas de la ficha (valuación/negocio/salud), para comparar en tabla."""
    return tablero_fundamentals()


@router.get("/reuters")
def reuters():
    """Tablero del tab REUTERS: quotes live del subyacente US (feed Eikon de la
    PC de oficina), SOLO los activos suscriptos. Cada fila: {ticker, ric, last,
    bid, ask, high, low, prev_close, volumen, var_pct, var_neta, ratio,
    ccl (None, pendiente), updated_at}."""
    return tablero_reuters()


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


@router.get("/renta-fija")
def renta_fija():
    """Radar de renta fija (pesos: tasa fija + CER) para el tab RENTA FIJA del
    panel de movers de TRADING: [{ticker_corto, last, tna, volumen}] ordenado
    por volumen del día desc. Click en una fila → carga la card."""
    return svc.get_renta_fija_radar()


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
