"""Router /api/research1816 — vista RESEARCH (nueva vista principal).

Doc madre: docs/VISTA_RESEARCH.md. Prefijo distinto del `/api/research` existente
(ese es la Análisis Fundamental de RV, gate renta-variable) para no pisarlo — la
vista Research es OTRA cosa (research macro de 1816: mails ahora, market data 1816
después). Gate a nivel router: módulo `research` (interno, JAMÁS invitado — REGLA #8).

Nivel 1 (pilar B — research escrito): sirve el timeline de mails y el buscador
full-text sobre `ia.research`. El pilar A (market data 1816) se suma cuando esté la
API key. Read-only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import require_module
from api.services import research_1816_sql as mkt
from api.services import research_sql as svc
from core.eikon_live import ficha as ficha_reuters
from core.eikon_live import tablero_fundamentals, tablero_reuters

router = APIRouter(
    prefix="/api/research1816",
    tags=["Research"],
    dependencies=[Depends(require_module("research"))],
)


@router.get("/mails")
def mails(
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict:
    """Timeline del research diario (mails de 1816), más nuevo primero.

    Returns:
        {items: [{id, fecha, fuente, asunto, tipo, cuerpo, destilado}], total}
    """
    return svc.listar_research(limit=limit, offset=offset)


@router.get("/mails/buscar")
def buscar(
    q: str = Query(..., min_length=2, description="texto a buscar (full-text español)"),
    limit: int = Query(30, ge=1, le=100),
) -> dict:
    """Búsqueda full-text sobre el cuerpo del research ('¿qué dijeron del BCRA?').

    Returns:
        {items: [{..., fragmento}], total, q}
    """
    return svc.buscar_research(q=q, limit=limit)


# ── Market Data 1816 (pilar A) — laboratorio de series/spreads ───────────────


@router.get("/universo")
def universo() -> dict:
    """Bonos de 1816 con series bajadas, agrupados por curva (para los selectores)."""
    return mkt.universo()


@router.get("/series")
def series(
    tickers: list[str] = Query(..., description="tickers (hasta 8)"),
    campo: str = Query("tea", description="tea | paridad | precioClean | duration"),
    desde: str | None = Query(None, description="YYYY-MM-DD (default 6 meses)"),
    hasta: str | None = Query(None, description="YYYY-MM-DD (default hoy)"),
) -> dict:
    """Serie del campo para cada ticker (overlay comparativo)."""
    return mkt.series(tickers=tickers, campo=campo, desde=desde, hasta=hasta)


# ── REUTERS (tab RENTA VARIABLE INTERNACIONAL) — feed Eikon de oficina ───────
# Movidos desde /api/trading/reuters* el 2026-07-18: la vista vive en /research
# (habilitada a toda la mesa) y trading queda ADMIN-ONLY — el gate acá es
# `research`, no `trading`. Backend del feed intacto (core/eikon_live).


@router.get("/reuters")
def reuters():
    """Tablero RV Internacional: quotes live del subyacente US (feed Eikon de la
    PC de oficina), SOLO los activos suscriptos. Cada fila: {ticker, ric, last,
    bid, ask, high, low, prev_close, volumen, var_pct, var_neta, ratio, ccl,
    updated_at}."""
    return tablero_reuters()


@router.get("/reuters/fundamentals")
def reuters_fundamentals():
    """Screener FUNDAMENTALS: una empresa por fila con las métricas de la ficha
    (valuación/negocio/salud), para comparar en tabla."""
    return tablero_fundamentals()


@router.get("/reuters/ficha")
def reuters_ficha(ticker: str):
    """FICHA de empresa: quote live + fundamentals curados + ratio del CEDEAR +
    velas diarias de 1 año (chart). 404 si el ticker no existe en ninguna fuente."""
    out = ficha_reuters(ticker)
    if out is None:
        raise HTTPException(404, f"sin datos para {ticker!r}")
    return out


@router.get("/spread")
def spread(
    a: str = Query(..., description="ticker A"),
    b: str = Query(..., description="ticker B"),
    campo: str = Query("tea", description="tea | paridad | precioClean | duration"),
    desde: str | None = Query(None),
    hasta: str | None = Query(None),
) -> dict:
    """Serie A−B en el tiempo + stats de valor relativo (percentil/z vs su historia)."""
    return mkt.spread(a=a, b=b, campo=campo, desde=desde, hasta=hasta)
