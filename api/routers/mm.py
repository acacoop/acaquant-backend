"""Router /api/mm — MM Workstation: replay + backtest + paper trading vivo.

Gate _MM (módulo `mm`) — solo admin por default. Trader / sales no
acceden hasta que el admin tilde `mm` en la matriz de roles.

Endpoints:
  GET  /api/mm/trades-dia        → trades de UN día para REPLAY
  GET  /api/mm/fechas            → fechas con actividad (selector REPLAY)
  POST /api/mm/backtest          → sweep multi-día sobre N spreads
  GET  /api/mm/live-snapshot     → book actual + trades nuevos (paper trading)
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import mm as svc

router = APIRouter(prefix="/api/mm", tags=["MM"])
logger = logging.getLogger("api.mm")


@router.get("/trades-dia")
def trades_dia(
    instrumento: str = Query(..., description="Ticker corto ('AL30D') o full"),
    fecha: str | None = Query(None, description="YYYY-MM-DD; default último día con actividad"),
    _email: str = Depends(get_user_email),
) -> dict[str, Any]:
    """Trades del día solicitado para alimentar el REPLAY del frontend.

    Si `fecha` no se pasa, devuelve el último día con actividad (≥100
    trades) de los últimos 30 días.
    """
    full = svc.resolver_instrumento_full(instrumento)
    if not fecha:
        fechas = svc.fechas_disponibles(full, dias_atras=30)
        if not fechas:
            return {"instrumento_full": full, "fecha": None, "trades": []}
        fecha = fechas[-1]
    try:
        trades = svc.fetch_trades_dia(instrumento_full=full, fecha=fecha)
    except Exception as e:
        logger.exception("trades_dia failed (instrumento=%s, fecha=%s)", instrumento, fecha)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"instrumento_full": full, "fecha": fecha, "trades": trades}


@router.get("/fechas")
def fechas(
    instrumento: str = Query(...),
    dias_atras: int = Query(30, ge=1, le=90),
    _email: str = Depends(get_user_email),
) -> dict[str, Any]:
    """Fechas con actividad de un instrumento — pobla el dropdown de día
    del REPLAY."""
    full = svc.resolver_instrumento_full(instrumento)
    return {
        "instrumento_full": full,
        "fechas":           svc.fechas_disponibles(full, dias_atras=dias_atras),
    }


@router.get("/live-snapshot")
def live_snapshot(
    instrumento: str = Query(..., description="Ticker corto ('AL30D') o full"),
    since_ts: str | None = Query(
        None,
        description="Cursor opaco (ISO datetime ART) devuelto por la llamada anterior. "
        "La primera llamada lo manda en None y solo recibe el cursor de salida.",
    ),
    _email: str = Depends(get_user_email),
) -> dict[str, Any]:
    """Snapshot vivo: book top-5 actual + trades nuevos desde `since_ts`.

    Pensado para ser polleado a ~1Hz por el frontend del paper trading.
    Solo lectura — no inserta nada en Mongo. Las dos colecciones que toca
    (Trading.MarketSnapshot y Trading.TimeSales) las puebla `engines/valores.py`.

    El frontend usa la respuesta para:
      - Calcular el mid (best bid + best ask) / 2.
      - Avanzar la cola de sus órdenes virtuales con cada trade en
        `new_trades` que matchee el price level.
      - Detectar fills (cola en 0 + trade en su precio).
      - Actualizar PnL = cash + inv * mid.
    """
    full = svc.resolver_instrumento_full(instrumento)
    try:
        return svc.fetch_live_snapshot(instrumento_full=full, since_ts=since_ts)
    except Exception as e:
        logger.exception(
            "live_snapshot failed (instrumento=%s, since_ts=%s)", instrumento, since_ts,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


class BacktestIn(BaseModel):
    instrumento: str
    desde: str | None = None        # YYYY-MM-DD; default hoy - dias_atras
    hasta: str | None = None        # YYYY-MM-DD; default hoy
    dias_atras: int = Field(10, ge=1, le=90,
                            description="Si desde/hasta no vienen, calcula los últimos N días")
    quote_size: float = Field(20_000, gt=0)
    skew_intensity: float = Field(1.0, ge=0, le=5)
    auto_skew: bool = True
    inv_cap: float = Field(200_000, gt=0)
    spreads: list[float] | None = Field(
        None, description="Override del sweep. Si None, usa default 7 spreads.",
    )


@router.post("/backtest")
def backtest(
    req: BacktestIn,
    _email: str = Depends(get_user_email),
) -> dict[str, Any]:
    """Sweep multi-día: por cada spread, corre 1 sim por día con actividad
    en el rango. Devuelve estadísticas agregadas (PnL medio, std, win rate,
    Sharpe, max DD) + detalle por día.

    Para 7 spreads × 10 días con 5000 trades cada día = ~350k iteraciones
    en Python puro. <2s típico, ≤5s en el peor caso. La cache de
    `fetch_trades_dia` reduce reads a Mongo cuando 8 users disparan
    el mismo backtest.
    """
    full = svc.resolver_instrumento_full(req.instrumento)
    hoy = datetime.now(UTC).date()
    hasta = req.hasta or hoy.isoformat()
    desde = req.desde or (hoy - timedelta(days=req.dias_atras)).isoformat()
    spreads = tuple(req.spreads) if req.spreads else svc.DEFAULT_SWEEP_SPREADS

    try:
        return svc.run_backtest_sweep(
            instrumento_full=full,
            desde=desde,
            hasta=hasta,
            quote_size=req.quote_size,
            skew_intensity=req.skew_intensity,
            auto_skew=req.auto_skew,
            inv_cap=req.inv_cap,
            spreads=spreads,
        )
    except Exception as e:
        logger.exception("backtest failed (req=%s)", req.model_dump())
        raise HTTPException(status_code=500, detail=str(e)) from e
