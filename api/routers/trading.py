"""Router /api/trading — vista TRADING (módulo `trading`, admin-only).

Pivots Floor Trader sobre el CEDEAR (ARS), la serie intradía del chart LIVE, el
radar de proximidad a pivote y el catálogo para el selector de las cards.
Lógica pura en api/services/trading_pivots.py. Ver [[project_vista_trading]].

Refactor 2026-09-01: se fue `/adr-zonas` (el chart ZONAS ADR salió de la vista;
los mismos niveles siguen en `/api/scanner/pivot-points`, panel MÉTRICAS de
Renta Variable) y el router `/api/estrategia` completo (tab ESTRATEGIA).
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from api.services import pnl_historico as pnl_hist_svc
from api.services import scanner_sql as scanner_svc
from api.services import trading_pivots as svc

# (Los endpoints /reuters* se mudaron a /api/research1816/reuters* el 2026-07-18:
# la vista REUTERS vive en /research y trading es ADMIN-ONLY — ver
# docs/RENTA_VARIABLE.md.)

router = APIRouter(prefix="/api/trading", tags=["Trading"])


@router.get("/pivots")
def pivots(tickers: str = Query("", description="CSV de ticker_corto de CEDEARs")):
    """Pivots del CEDEAR por ticker. Cada item: {ticker, last, fecha, high, low,
    close, pivots:{pp,r1,r2,r3,s1,s2,s3}} o {ticker, last, sin_datos:true}."""
    lista = [t.strip() for t in tickers.split(",") if t.strip()]
    return svc.get_pivots(tickers=lista)


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


# ── PNL HISTÓRICO (cuaderno manual, ver api/services/pnl_historico.py) ──────────
class PnlHistIn(BaseModel):
    fecha: str                       # 'YYYY-MM-DD' (día hábil)
    monto: float | None = None       # None/vacío borra la celda
    cuenta: str = "General"


@router.get("/pnl-historico")
def pnl_historico(cuenta: str = Query("General", description="cuenta libre; default General")):
    """Días hábiles desde el 1-jul-2026 hasta fin del mes actual, con el PnL
    manual de la cuenta + acumulado total y mensual. Ver el service para el shape."""
    return pnl_hist_svc.listar(cuenta=cuenta)


@router.post("/pnl-historico")
def pnl_historico_guardar(payload: PnlHistIn):
    """Carga/edita el PnL de un día. monto vacío → borra la fila."""
    return pnl_hist_svc.guardar(fecha=payload.fecha, monto=payload.monto, cuenta=payload.cuenta)
