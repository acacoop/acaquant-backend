"""Router /api/trading — vista TRADING (módulo `trading`, admin-only).

Por ahora: pivots Floor Trader sobre el CEDEAR (ARS) + el catálogo de CEDEARs
para el selector de instrumento de las cards. Lógica pura en
api/services/trading_pivots.py. Ver [[project_vista_trading]].
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


@router.get("/adr-zonas")
def adr_zonas(ticker: str, dias: int = 180):
    """Velas DIARIAS del ADR/subyacente USD + los 4 timeframes de pivots
    (diario/semanal/mensual/anual) para el chart ZONAS de TRADING. Mismos
    niveles que el panel MÉTRICAS de Renta Variable, pero graficables.

    Vive acá (y no en /api/scanner) porque TRADING es su propio módulo RBAC:
    un usuario con `trading` y sin `renta-variable` igual tiene que verlo.

    Shape: {ticker, underlying, last, last_source, velas:[{t,o,h,l,c}],
    frames:{semanal:{label,fecha_desde,fecha_hasta,h,l,c,levels}, ...}}
    o {sin_datos: true} si el ticker no tiene serie USD (ej. un bono).
    """
    return svc.get_adr_zonas(ticker=ticker, dias=dias)


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
