"""Router /api/scanner — vista Scanner del módulo Renta Variable.

Consumido por el frontend acaquant-web /renta-variable (tab Scanner).
RBAC `renta-variable` — actualmente admin-only (Manager.RoleMatrix).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth import require_module
from api.services import rv_motor
from api.services import scanner as svc

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
            ticker_corto, underlying, ratio_cedear,
            sector, industria, region, pais,
            last, open, high, low, close,
            intraday_pct, vs_1d_pct, vs_1d_usd_pct,
            updated_at
    """
    return svc.get_cedears_scanner()


@router.get("/ccl")
def ccl_live():
    """CCL live + variación 1D — para el KPI del shell de Renta Variable.

    Returns:
        {value: float|None, vs_1d_pct: float|None, ts: str|None}
    """
    return svc.get_ccl_live()


@router.get("/returns/{ticker}")
def returns(ticker: str):
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
    return svc.get_ticker_returns(ticker=ticker)


@router.get("/quant/{ticker}")
def quant_stats(ticker: str):
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
    return svc.get_quant_stats(ticker=ticker)


@router.get("/pivot/{ticker}")
def pivot_points(ticker: str):
    """4 timeframes de pivot points (diario/semanal/mensual/anual) sobre
    el subyacente USD del CEDEAR. Lee de Trading.PreciosAcciones, que
    alimenta el cron jobs.precios_acciones_daily.

    El `ticker` que llega del frontend es ticker_corto (BYMA). Se
    resuelve el underlying antes de queryar la serie (caso YPFD → YPF).
    """
    return svc.get_pivot_points(ticker=ticker)


@router.get("/correlaciones")
def correlaciones(ventana: int = 252, tickers: str | None = None):
    """Matriz de correlación de retornos diarios — motor de la Mesa de
    Estrategia (ver docs/wip_mesa_estrategia_rv.md).

    Sin `tickers` usa todo el universo de Renta Variable. `tickers` opcional
    es un CSV de ticker_corto. `ventana` = días hábiles comunes (default 252).
    Base del hedge-finder y de la optimización de carteras.
    """
    tks = (
        tuple(t.strip().upper() for t in tickers.split(",") if t.strip())
        if tickers else None
    )
    return rv_motor.get_correlation_matrix(tickers=tks, ventana_dias=ventana)


@router.get("/trade-analysis")
def trade_analysis(ticker: str, monto: float = 1_000_000, direccion: str = "long"):
    """Análisis de un trade individual — caracterización de riesgo +
    hedge-finder. Módulo 1 de la Mesa de Estrategia
    (ver docs/wip_mesa_estrategia_rv.md). `monto` en USD, `direccion` long|short.
    """
    return rv_motor.get_trade_analysis(
        ticker=ticker, monto=monto, direccion=direccion,
    )
