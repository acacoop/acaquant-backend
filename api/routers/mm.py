"""Router /api/mm — MM Microstructure (Cartea cap 1-4 sobre AL30 CI live + histórico).

Gate _MM (módulo `mm` — admin only por default, abrir a trader/sales editando
la matriz desde /manager).

6 endpoints, 3 niveles de granularidad:

  Live (cap 1, refresh ~1Hz):
    GET /api/mm/live              → book + cap 1 metrics + last trade enriched
    GET /api/mm/tape              → trades de la ventana, cap 4 enriched

  Offline (cap 2-4, cache 5-10 min):
    GET /api/mm/intraday          → buckets 1-min con NOF, qES, walking, vol
    GET /api/mm/impact            → OLS sobre histórico para `b` y `k`
    GET /api/mm/smile             → smile intradiario (forma U) sobre N días
    GET /api/mm/stylized-facts    → kurt/skew/ACF sobre retornos del mid
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import get_user_email
from api.services import mm_microstructure as svc

router = APIRouter(prefix="/api/mm", tags=["MM Microstructure"])
logger = logging.getLogger("api.mm")


@router.get("/live")
def live(
    ticker: str = Query(svc.DEFAULT_TICKER, description="Ticker full ROFEX"),
    _email: str = Depends(get_user_email),
) -> dict[str, Any]:
    """Snapshot del estado actual: book top-5 + métricas cap 1 + último trade.

    Refresh natural ~1Hz (depende del motor `engines/order_book_l2.py`).
    Sin cache — cada llamada lee fresco de Mongo.
    """
    try:
        return svc.get_live(ticker=ticker)
    except Exception as e:
        logger.exception("mm/live failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/tape")
def tape(
    ticker: str = Query(svc.DEFAULT_TICKER),
    desde: str | None = Query(None, description="ISO datetime UTC opcional"),
    hasta: str | None = Query(None, description="ISO datetime UTC opcional"),
    ventana_min: int = Query(30, ge=1, le=180,
                             description="Si desde/hasta no vienen, últimos N min"),
    limit: int = Query(500, ge=10, le=2000,
                       description="Hard cap de trades devueltos"),
    _email: str = Depends(get_user_email),
) -> dict[str, Any]:
    """Trades enriquecidos: cada uno con effective spread (en bps),
    Lee-Ready side y cross-check con `trade.side`, walking flag, mid del
    momento.
    """
    try:
        return svc.get_tape(
            ticker=ticker, desde=desde, hasta=hasta,
            ventana_min=ventana_min, limit=limit,
        )
    except Exception as e:
        logger.exception("mm/tape failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/intraday")
def intraday(
    ticker: str = Query(svc.DEFAULT_TICKER),
    fecha: str | None = Query(None, description="YYYY-MM-DD; default último día con trades"),
    bucket_min: int = Query(1, ge=1, le=60),
    _email: str = Depends(get_user_email),
) -> dict[str, Any]:
    """Buckets intradía con métricas cap 4 agregadas.

    Por bucket: NOF (Lee-Ready), qES (quantity-weighted ES), walking %,
    realized vol (sobre mid), volume, n trades, mid_close.

    Útil para: ver flujo neto del día, identificar momentos de alta toxicidad
    (qES alto), zonas de bajo flow (NOF cerca de 0), etc.
    """
    try:
        return svc.get_intraday(ticker=ticker, fecha=fecha, bucket_min=bucket_min)
    except Exception as e:
        logger.exception("mm/intraday failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/impact")
def impact(
    ticker: str = Query(svc.DEFAULT_TICKER),
    desde: str | None = Query(None, description="YYYY-MM-DD"),
    hasta: str | None = Query(None, description="YYYY-MM-DD"),
    dias: int = Query(5, ge=1, le=60,
                      description="Si desde/hasta no vienen, últimos N días con trades"),
    bucket_min: int = Query(1, ge=1, le=15),
    _email: str = Depends(get_user_email),
) -> dict[str, Any]:
    """Estimación empírica del cap 4 sobre N días:

    - Permanent impact `b`: ΔS_n = b · π_n + ε  (Δmid del bucket vs NOF).
    - Temporary impact `k`: |price − mid| = k · Q + ε  (per trade).

    OLS sin intercepto + winsorización 1% en cada cola. R² + n para evaluar
    confiabilidad. Pendiente: Huber/RLM para robustez.
    """
    try:
        return svc.get_impact(
            ticker=ticker, desde=desde, hasta=hasta, dias=dias, bucket_min=bucket_min,
        )
    except Exception as e:
        logger.exception("mm/impact failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/smile")
def smile(
    ticker: str = Query(svc.DEFAULT_TICKER),
    dias: int = Query(5, ge=2, le=60),
    bucket_min: int = Query(30, ge=15, le=60),
    _email: str = Depends(get_user_email),
) -> dict[str, Any]:
    """Smile intradiario: avg volumen + avg vol realizada por bucket de
    `bucket_min` minutos sobre los últimos N días con trades.

    Esperás forma U: pico apertura, mínimo mediodía, pico mayor cierre.
    """
    try:
        return svc.get_smile(ticker=ticker, dias=dias, bucket_min=bucket_min)
    except Exception as e:
        logger.exception("mm/smile failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/stylized-facts")
def stylized_facts(
    ticker: str = Query(svc.DEFAULT_TICKER),
    desde: str | None = Query(None, description="YYYY-MM-DD"),
    hasta: str | None = Query(None, description="YYYY-MM-DD"),
    dias: int = Query(5, ge=2, le=60),
    bucket_min: int = Query(1, ge=1, le=15),
    _email: str = Depends(get_user_email),
) -> dict[str, Any]:
    """Cap 3: kurtosis, skewness, ACF lag-1 (mid vs last), persistencia
    ACF de |r|, Jarque-Bera con p-value.

    Devuelve también `interpretacion` — lecturas en español sobre lo que
    los números significan (colas pesadas, bid-ask bounce, volatility
    clustering, eficiencia direccional).
    """
    try:
        return svc.get_stylized_facts(
            ticker=ticker, desde=desde, hasta=hasta, dias=dias, bucket_min=bucket_min,
        )
    except Exception as e:
        logger.exception("mm/stylized-facts failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
