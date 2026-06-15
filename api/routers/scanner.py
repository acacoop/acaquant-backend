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


@router.get("/cedears/trades")
def cedears_trades(ticker: str, limite: int = 200):
    """Time & Sales intradía del CEDEAR (tape). Trades inferidos por el motor
    en Trading.CedearsTimeSales (se vacía al cierre). `ticker` = ticker_corto.

    Returns:
        list[{timestamp, price, size, side, money}] desc por timestamp.
    """
    return svc.get_cedears_trades(ticker=ticker, limite=limite)


@router.get("/cedears/intraday")
def cedears_intraday(ticker: str):
    """Serie intradía por minuto (OHLC + vol) del CEDEAR desde el Time & Sales
    de hoy. Para el chart LIVE del Scanner (mismo feed que tabla/tape, sin delay).
    `ticker` = ticker_corto.

    Returns:
        list[{t, o, h, l, c, vol}] asc por minuto (UTC ISO).
    """
    return svc.get_cedears_intraday(ticker=ticker)


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


@router.get("/correlaciones")
def correlaciones(ventana: int = 252, tickers: str | None = None):
    """Matriz de correlación de retornos diarios — motor de la Mesa de
    Estrategia (UI: acaquant-web /retorno → CORRELACIONES).

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
    hedge-finder. Módulo 1 de la Mesa de Estrategia (UI: acaquant-web
    /retorno → TRADE LAB). `monto` en USD, `direccion` long|short.
    """
    return rv_motor.get_trade_analysis(
        ticker=ticker, monto=monto, direccion=direccion,
    )


@router.get("/book-analysis")
def book_analysis(posiciones: str = ""):
    """Análisis de un book entero — Módulo 2 de la Mesa de Estrategia
    (UI: acaquant-web /retorno → BOOK & RIESGO).

    `posiciones` es un CSV de `ticker:notional` (notional en USD, negativo =
    short). Ej: `NVDA:1000000,AMD:-500000`. Devuelve exposición, concentración,
    riesgo agregado y contribución de riesgo.
    """
    items: list[tuple[str, float]] = []
    for parte in posiciones.split(","):
        parte = parte.strip()
        if not parte or ":" not in parte:
            continue
        tk, _, monto = parte.partition(":")
        try:
            items.append((tk.strip().upper(), float(monto)))
        except ValueError:
            continue
    return rv_motor.get_book_analysis(posiciones=tuple(items))
