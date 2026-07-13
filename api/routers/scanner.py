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

from fastapi import APIRouter, Depends, Query

from api.auth import require_admin, require_module
from api.services import day_trading as svc_dt
from api.services import scanner as svc
from api.services import scanner_sql as svc_sql
from api.services.operaciones_view import motor as _motor

router = APIRouter(
    prefix="/api/scanner",
    tags=["Scanner"],
    dependencies=[Depends(require_module("renta-variable"))],
)


def _scanner(_engine: str | None):
    """Service del scanner (SQL o Mongo) según flag SCANNER_SQL / override ?_engine.
    Default Mongo. SQL lee mercado.{cedears,cedears_snapshot,adr_snapshot,
    precios_acciones,day_trading_stats}; el tape intradía + CCL live caen a Mongo
    (no tienen fuente SQL con paridad — ver scanner_sql)."""
    return svc_sql if _motor(_engine, "SCANNER_SQL") == "sql" else svc


@router.get("/cedears")
def cedears_scanner(_engine: str | None = Query(None, include_in_schema=False)):
    """Lista de CEDEARs activos con master + snapshot live join.

    Returns:
        list[dict] con shape:
            ticker_corto, underlying, ratio_cedear,
            sector, industria, region, pais,
            last, open, high, low, close,
            intraday_pct, vs_1d_pct, vs_1d_usd_pct,
            updated_at
    """
    return _scanner(_engine).get_cedears_scanner()


@router.get("/cedears/trades")
def cedears_trades(ticker: str, limite: int = 200,
                   _engine: str | None = Query(None, include_in_schema=False)):
    """Time & Sales intradía del CEDEAR (tape). Trades inferidos por el motor
    en Trading.CedearsTimeSales (se vacía al cierre). `ticker` = ticker_corto.

    Returns:
        list[{timestamp, price, size, side, money}] desc por timestamp.
    """
    return _scanner(_engine).get_cedears_trades(ticker=ticker, limite=limite)


@router.get("/cedears/intraday")
def cedears_intraday(ticker: str, _engine: str | None = Query(None, include_in_schema=False)):
    """Serie intradía por minuto (OHLC + vol) del CEDEAR desde el Time & Sales
    de hoy. Para el chart LIVE del Scanner (mismo feed que tabla/tape, sin delay).
    `ticker` = ticker_corto.

    Returns:
        list[{t, o, h, l, c, vol}] asc por minuto (UTC ISO).
    """
    return _scanner(_engine).get_cedears_intraday(ticker=ticker)


@router.get("/ccl")
def ccl_live(_engine: str | None = Query(None, include_in_schema=False)):
    """CCL live + variación 1D — para el KPI del shell de Renta Variable.

    Returns:
        {value: float|None, vs_1d_pct: float|None, ts: str|None}
    """
    return _scanner(_engine).get_ccl_live()


@router.get("/returns/{ticker}")
def returns(ticker: str, _engine: str | None = Query(None, include_in_schema=False)):
    """Serie de retornos diarios del último año (~252 puntos) desde
    Trading.PreciosAcciones. Para el histograma del Scanner.

    Returns:
        {
          ticker,
          returns: [r1, r2, ...],   # aritméticos diarios
          last_return: float | None,
          last_fecha: str | None,
        }
    """
    return _scanner(_engine).get_ticker_returns(ticker=ticker)


@router.get("/quant/{ticker}")
def quant_stats(ticker: str, _engine: str | None = Query(None, include_in_schema=False)):
    """Stats rolling (beta/alpha/corr vs SPY y QQQ + vol realizada 30d/60d)
    sobre el subyacente USD del CEDEAR. Lee de Trading.PreciosAcciones.

    Returns:
        {
          ticker, last, n_observations,
          beta:  {spy, qqq},
          alpha: {spy, qqq},   # anualizada
          corr:  {spy, qqq},
          vol:   {d30, d60},
        }
    """
    return _scanner(_engine).get_quant_stats(ticker=ticker)


@router.get("/pivot/{ticker}")
def pivot_points(ticker: str, _engine: str | None = Query(None, include_in_schema=False)):
    """4 timeframes de pivot points (diario/semanal/mensual/anual) sobre
    el subyacente USD del CEDEAR. Lee de Trading.PreciosAcciones, que
    alimenta el cron jobs.precios_acciones_daily.

    El `ticker` que llega del frontend es ticker_corto (BYMA). Se
    resuelve el underlying antes de queryar la serie (caso YPFD → YPF).
    """
    return _scanner(_engine).get_pivot_points(ticker=ticker)


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
# Los services de rv_motor SIGUEN VIVOS — los usan las tools del MCP
# (correlacion / trade_analysis / book_analysis, ver docs/MCP_TOOLS.md).
