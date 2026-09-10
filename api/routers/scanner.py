"""Router /api/scanner — vista Scanner del módulo Renta Variable.

Consumido por el frontend acaquant-web /renta-variable (tab Scanner).
RBAC a nivel router: `renta-variable` (Scanner de CEDEARs).

EXCEPCIÓN admin-only: los endpoints de TRADE LAB (`/day-trading` y
`/companeros`) llevan ADEMÁS `require_admin` a nivel ruta → solo rol admin,
no delegable desde el panel. Alimentan la vista propia acaquant-web
`/trade-lab` (sacada de la tab ESTRATEGIA el 2026-06-11 por exposición a
roles no-admin).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth import require_admin, require_module
from api.services import day_trading as svc_dt
from api.services import scanner as svc
from api.services import scanner_sql as svc_sql

router = APIRouter(
    prefix="/api/scanner",
    tags=["Scanner"],
    dependencies=[Depends(require_module("renta-variable"))],
)


@router.get("/cedears")
def cedears_scanner():
    """Lista de CEDEARs activos con master + snapshot live join.

    Returns:
        list[dict] con shape:
            ticker_corto, nombre, underlying, ratio_cedear,
            last, open, high, low, close, bid, offer, spread, spread_pct,
            vwap, volume, total_money,
            intraday_pct, vs_1d_pct, vs_1d_usd_pct, updated_at
        (2026-09-10: sin `adr_*`, `rubro` ni `es_ia` — nadie los consumía y
        viajaban cada 2 s en todas las pestañas de la mesa)
    """
    return svc_sql.get_cedears_scanner()


@router.get("/ccl")
def ccl_live():
    """CCL live + variación 1D — para el KPI del shell de Renta Variable.

    Returns:
        {value: float|None, vs_1d_pct: float|None, ts: str|None}
    """
    return svc.get_ccl_live()


@router.get("/pivot/{ticker}")
def pivot_points(ticker: str):
    """4 timeframes de pivot points (diario/semanal/mensual/anual) sobre
    el subyacente USD del CEDEAR. Lee de mercado.precios_acciones, que
    alimenta el cron jobs.precios_acciones_daily. Lo consume la ventana
    PIVOTS de TRADING → MONITOR (acaquant-web `pivot-points-panel.tsx`).

    El `ticker` que llega del frontend es ticker_corto (BYMA). Se
    resuelve el underlying antes de queryar la serie (caso YPFD → YPF).
    NO confundir con `/api/trading/pivots` (pivots del CEDEAR en ARS).
    """
    return svc_sql.get_pivot_points(ticker=ticker)


@router.get("/day-trading", dependencies=[Depends(require_admin)])
def day_trading(objetivo: float = 0.5):
    """Ranking intradía de CEDEARs para scalping — TRADE LAB (UI: acaquant-web
    /trade-lab). ADMIN-ONLY (`require_admin`). `objetivo` = tamaño del movimiento buscado en %
    (0.1–5). Por papel: vueltas zigzag ≥ objetivo hechas HOY (tape por minuto),
    rango del día, posición en el rango, momentum 15', vs VWAP, spread e idea
    heurística LONG/SHORT con motivo.
    """
    return svc_dt.get_day_trading(objetivo_pct=objetivo)


@router.get("/companeros/{ticker}", dependencies=[Depends(require_admin)])
def companeros(ticker: str, n: int = 6):
    """Con qué papeles se mueve un ticker (correlación diaria del subyacente
    USD): top `n` que acompañan y top `n` que van al revés. Para el panel
    'SE MUEVE CON / CONTRA' del TRADE LAB. ADMIN-ONLY (`require_admin`).
    """
    return svc_dt.get_companeros(ticker=ticker, n=n)


# Los endpoints HTTP de la Mesa de Estrategia (/correlaciones,
# /trade-analysis, /book-analysis) se ELIMINARON (2026-07-13): sus tabs de UI
# ya no existen (COBERTURAS fue la última) y ningún frontend los consumía.
# Sus services quedaron vivos porque los usaban las tools del MCP, y con el MCP
# borrado (2026-08-28) se fueron `rv_motor.get_trade_analysis` y
# `get_book_analysis`. Lo que SÍ sobrevive es `rv_motor.get_correlation_matrix`:
# lo usa `day_trading` para `GET /companeros/{ticker}`, acá arriba.
