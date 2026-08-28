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
from api.cache import cached
from api.services import research_1816_sql as mkt
from api.services import research_sql as svc
from core.eikon_live import agregado_fundamentals, tablero_fundamentals, tablero_reuters
from core.eikon_live import ficha as ficha_reuters
from core.eikon_segmentos import agregado_segmentos
from core.eikon_segmentos import segmentos as segmentos_empresa


# Cache COMPARTIDO del tablero (perf 2026-07-18, medido: la
# query LATERAL cuesta ~68ms y el front la pollea cada 5s POR USUARIO → sin
# cache, N usuarios = N×68ms cada 5s). TTL 4s < poll 5s: sigue siendo "live"
# (a lo sumo 4s de rezago) pero todos los usuarios comparten UNA query.
@cached(ttl=4)
def _tablero_cached() -> list[dict]:
    return tablero_reuters()


# Fundamentals cambian ~1 vez por día (el feed los manda a la mañana) → 60s.
@cached(ttl=60)
def _fundamentals_cached() -> list[dict]:
    return tablero_fundamentals()


# El agregado recorre las series de TODAS las empresas y suma en Python → es la
# consulta más cara del tab. Misma frecuencia de cambio que los fundamentals
# (1 vez por día) → 60s, cacheado POR combinación de filtros.
@cached(ttl=60)
def _agregado_cached(periodo: str, rubro: str | None, canasta: str) -> dict:
    return agregado_fundamentals(periodo=periodo, rubro=rubro, canasta=canasta)


# Segmentos: los escribe el feed 1 vez por día → mismo TTL que fundamentals.
@cached(ttl=60)
def _segmentos_cached(ticker: str, tipo: str, periodo: str) -> dict:
    return segmentos_empresa(ticker=ticker, tipo=tipo, periodo=periodo)


@cached(ttl=60)
def _segmentos_agregado_cached(tipo: str, periodo: str, rubro: str | None,
                               tickers: str | None) -> dict:
    # `tickers` viaja como string separado por comas para que el cache key sea
    # hasheable (una lista no lo es).
    lista = [t for t in (tickers or "").split(",") if t.strip()]
    return agregado_segmentos(tipo=tipo, periodo=periodo, rubro=rubro, tickers=lista)

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
        {items: [{id, fecha, fuente, asunto, tipo, cuerpo}], total}
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
    updated_at}. Cache compartido 4s (ver _tablero_cached)."""
    return _tablero_cached()


@router.get("/reuters/fundamentals")
def reuters_fundamentals():
    """Screener FUNDAMENTALS: una empresa por fila con las métricas de la ficha
    (valuación/negocio/salud), para comparar en tabla. Cache compartido 60s."""
    return _fundamentals_cached()


@router.get("/reuters/fundamentals/agregado")
def reuters_fundamentals_agregado(
    periodo: str = Query("anual", description="anual (5 años) | trimestral (8 trimestres)"),
    rubro: str | None = Query(None, description="agrega solo las empresas de ese rubro"),
    canasta: str = Query("constante", description="constante | todas"),
):
    """El universo del feed SUMADO en el tiempo: una fila por período de
    calendario con Σ ingresos / EBITDA / resultado / FCF / capex / deuda / caja
    y los márgenes del agregado. `canasta=constante` suma solo las empresas con
    datos en TODOS los períodos (una curva comparable); `todas` suma lo que haya.
    Cache compartido 60s por combinación de filtros."""
    return _agregado_cached(periodo=periodo, rubro=rubro, canasta=canasta)


@router.get("/reuters/ficha")
def reuters_ficha(ticker: str):
    """FICHA de empresa: quote live + fundamentals curados + ratio del CEDEAR +
    velas diarias de 1 año (chart). 404 si el ticker no existe en ninguna fuente."""
    out = ficha_reuters(ticker)
    if out is None:
        raise HTTPException(404, f"sin datos para {ticker!r}")
    return out


@router.get("/reuters/segmentos")
def reuters_segmentos(
    ticker: str,
    tipo: str = Query("negocio", description="negocio | geografico"),
    periodo: str = Query("anual", description="anual | trimestral"),
):
    """De dónde salen las ventas de una empresa: desglose por SEGMENTO DE
    NEGOCIO o por REGIÓN, período a período, listo para apilar.

    OJO conceptual: el "segmento de negocio" es el que publica la empresa —
    Apple y Coca-Cola reportan por región, NVDA y Rocket Lab por producto.
    Las filas de eliminaciones/corporate vienen marcadas en `ajustes` (no son
    negocio, pero sin ellas la suma no cierra contra los ingresos totales).
    Cache 60s: cambia 1 vez por día con la pasada del feed."""
    return _segmentos_cached(ticker=ticker, tipo=tipo, periodo=periodo)


@router.get("/reuters/segmentos/agregado")
def reuters_segmentos_agregado(
    tipo: str = Query("negocio", description="negocio | geografico"),
    periodo: str = Query("anual", description="anual | trimestral"),
    rubro: str | None = Query(None, description="solo las empresas de ese rubro"),
    tickers: str | None = Query(None, description="lista separada por comas"),
):
    """De dónde sale la plata en el universo filtrado: ranking de segmentos (o
    de países) con la suma del último período de cada empresa, su % del total y
    de qué empresas viene. Acompaña al filtro de RUBRO y al buscador.

    Las filas de eliminaciones/corporate NO entran al ranking (no son un negocio
    ni un país) — viajan sumadas aparte en `ajustes`."""
    return _segmentos_agregado_cached(tipo=tipo, periodo=periodo, rubro=rubro,
                                      tickers=tickers)


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
