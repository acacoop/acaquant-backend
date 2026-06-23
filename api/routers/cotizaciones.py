"""Router Cotizaciones — thin wrappers sobre la capa de servicio.

La lógica vive en `api/services/*`. Acá solo declaramos endpoints FastAPI
que parsean query params y delegan al service. Separación por dominio:

- macro           → series BCRA (BADLAR/CER/DOLAR) + dólar MEP
- repo            → caución (ARS + USD)
- derivados       → futuros DLR + forwards + breakevens
- renta_fija      → MarketSnapshot + TimeSales + curvas
- opciones        → chain + meta + históricos
- argy            → panel multi-métrica con returns

Motivo de la capa de servicio: `api/agent/tools.py::dispatch` la invoca
directamente, sin loopback HTTP.
"""
import os

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import require_module
from api.services import argy as svc_argy
from api.services import derivados as svc_der
from api.services import fair_value as svc_fv
from api.services import macro as svc_macro
from api.services import macro_sql as svc_macro_sql
from api.services import mercado_hist_sql as svc_mhist
from api.services import opciones as svc_opt
from api.services import rem as svc_rem
from api.services import rem_sql as svc_rem_sql
from api.services import renta_fija as svc_rf
from api.services import renta_fija_sql as svc_rf_sql
from api.services import repo as svc_repo

router = APIRouter(prefix="/api/cotizaciones", tags=["Cotizaciones"])


def _rem(engine: str | None):
    """Selector de motor REM: SQL (macro.rem) o Mongo (Trading.REM). SQL si
    `?_engine=sql` o el flag global `REM_SQL=1`; Mongo en cualquier otro caso
    (default). El path Mongo queda intacto → rollback = sacar la env + restart."""
    use_sql = engine == "sql" or (engine != "mongo" and os.getenv("REM_SQL") == "1")
    return svc_rem_sql if use_sql else svc_rem


def _macro(engine: str | None):
    """Selector de motor de series macro: SQL (macro.series_macro) o Mongo
    (Trading.*). SQL si `?_engine=sql` o flag `MACRO_SQL=1`; default Mongo. El
    path SQL delega a Mongo lo no-migrado (mep/ccl/canje, ticker, caución)."""
    use_sql = engine == "sql" or (engine != "mongo" and os.getenv("MACRO_SQL") == "1")
    return svc_macro_sql if use_sql else svc_macro


def _hist(engine: str | None, mongo_svc):
    """Selector de históricos de mercado (futuros DLR / forwards / breakevens /
    caución): SQL (`mercado.mercado_hist`) si `?_engine=sql` o flag
    `MERCADO_HIST_SQL=1`; si no, el servicio Mongo original (`mongo_svc`)."""
    use_sql = engine == "sql" or (engine != "mongo" and os.getenv("MERCADO_HIST_SQL") == "1")
    return svc_mhist if use_sql else mongo_svc


def _rf(engine: str | None):
    """Selector de renta fija LIVE: SQL (mercado.market_snapshot + curvas +
    snapshots_cierre_hist) si `?_engine=sql` o flag `RENTA_FIJA_SQL=1`; Mongo
    (Trading.*) en cualquier otro caso. El path Mongo queda intacto → rollback =
    sacar la env + restart."""
    use_sql = engine == "sql" or (engine != "mongo" and os.getenv("RENTA_FIJA_SQL") == "1")
    return svc_rf_sql if use_sql else svc_rf


def _fwbe(engine: str | None):
    """Forwards/breakevens LIVE: SQL (la fila más reciente de mercado_hist, fresca por
    SNAPSHOT_SQL) si `?_engine=sql` o `RENTA_FIJA_SQL=1`; Mongo (ForwardsLive/BreakevensLive)
    si no. MISMO flag que el resto de renta fija → un solo switch para toda la pantalla."""
    use_sql = engine == "sql" or (engine != "mongo" and os.getenv("RENTA_FIJA_SQL") == "1")
    return svc_mhist if use_sql else svc_der


# ── Series BCRA (BADLAR, CER, DOLAR) ──


@router.get("/badlar")
def listar_badlar(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    _engine: str | None = Query(None, include_in_schema=False),
):
    return _macro(_engine).get_badlar(desde=desde, hasta=hasta)


@router.get("/cer")
def listar_cer(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    _engine: str | None = Query(None, include_in_schema=False),
):
    return _macro(_engine).get_cer(desde=desde, hasta=hasta)


@router.get("/dolar")
def listar_dolar(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    _engine: str | None = Query(None, include_in_schema=False),
):
    return _macro(_engine).get_dolar(desde=desde, hasta=hasta)


# ── Dólar MEP ──


@router.get("/mep")
def ultimo_mep():
    return svc_macro.get_ultimo_mep()


@router.get("/historico/mep")
def historico_mep(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    return svc_macro.get_historico_mep(desde=desde, hasta=hasta)


@router.get("/historico/dolares")
def historico_dolares(
    desde:        str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta:        str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    ventana_dias: int        = Query(7, description="Default ventana si no hay desde/hasta"),
):
    """Serie histórica MEP + CCL + oficial para el chart de ARGY."""
    return svc_macro.get_historico_dolares(
        desde=desde, hasta=hasta, ventana_dias=ventana_dias,
    )


# ── ARGY (panel de control: MEP/CCL/canje/cauciones con returns) ──


@router.get("/argy")
def argy():
    return svc_argy.get_argy_with_returns()


# ── Caución (mercado repo) ──


@router.get("/caucion")
def caucion(
    moneda: str | None = Query(None, description="ARS o USD; vacío = ambas"),
):
    return svc_repo.get_caucion(moneda=moneda)


@router.get("/historico/caucion")
def historico_caucion(
    moneda: str | None = Query(None, description="ARS o USD"),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    _engine: str | None = Query(None, include_in_schema=False),
):
    return _hist(_engine, svc_repo).get_historico_caucion(moneda=moneda, desde=desde, hasta=hasta)


# ── Futuros DLR ──


@router.get("/futuros-dlr")
def futuros_dlr():
    return svc_der.get_futuros_dlr()


@router.get("/historico/futuros-dlr")
def historico_futuros_dlr(
    ticker: str | None = Query(None, description="Filtrar por ticker (DLR/MMMYY)"),
    desde:  str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta:  str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    _engine: str | None = Query(None, include_in_schema=False),
):
    return _hist(_engine, svc_der).get_historico_futuros_dlr(ticker=ticker, desde=desde, hasta=hasta)


# ── Forwards ──


@router.get("/forwards")
def listar_forwards(
    curva: str | None = Query(None, description="Filtrar por curva (tasa_fija/cer)"),
    _engine: str | None = Query(None, include_in_schema=False),
):
    return _fwbe(_engine).get_forwards(curva=curva)


@router.get("/historico/forwards")
def historico_forwards(
    curva: str | None = Query(None, description="Filtrar por curva (tasa_fija/cer)"),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    _engine: str | None = Query(None, include_in_schema=False),
):
    return _hist(_engine, svc_der).get_historico_forwards(curva=curva, desde=desde, hasta=hasta)


@router.get("/forwards-zscore")
def forwards_zscore(
    curva: str | None = Query(None, description="Filtrar por curva (tasa_fija/cer)"),
):
    """Coeficientes (media, desvío, n_obs) por par para z-scoreo de forwards.

    El front computa z = (forward_live − media) / desvío en cada tick. El doc
    se refresca 1x/día por jobs/forwards_zscore.py post-cierre del motor.
    Pares con n_obs<20 o desvío≈0 no aparecen en `stats` (front los pinta n/d).
    """
    return svc_der.get_forwards_zscore(curva=curva)


# ── Fair Value (curva cuadrática + residuos + z-scores) ──


@router.get("/fair-value")
def fair_value_live(
    curva: str = Query(..., description="tasa_fija | cer"),
):
    """Live: β del último cierre + TEAs vivas → residuos + z_estatico recompute.

    z_temporal viene del cierre (no se recalcula intra-día).
    """
    return svc_fv.get_fair_value_live(curva=curva)


@router.get("/fair-value/cierre")
def fair_value_cierre(
    curva: str = Query(..., description="tasa_fija | cer"),
    fecha: str | None = Query(None, description="YYYY-MM-DD (default: último)"),
):
    """Snapshot persistido del cierre de FairValueResiduos."""
    return svc_fv.get_fair_value_cierre(curva=curva, fecha=fecha)


@router.get("/fair-value/historico")
def fair_value_historico(
    ticker: str = Query(..., description="Ticker completo (MERV - XMEV - X - 24hs)"),
    dias: int = Query(60, ge=1, le=365),
):
    """Serie diaria del residuo del bono — alimenta el modal de drill-down."""
    return svc_fv.get_fair_value_historico_bono(ticker=ticker, dias=dias)


# ── Breakevens ──


@router.get("/breakevens")
def listar_breakevens(_engine: str | None = Query(None, include_in_schema=False)):
    return _fwbe(_engine).get_breakevens()


@router.get("/snapshot-live")
def snapshot_live(_engine: str | None = Query(None, include_in_schema=False)) -> dict:
    """Bundle live de la pantalla RENTA FIJA: renta_fija + forwards +
    breakevens en una sola respuesta. Cada bloque viene del cache TTL
    propio del service (renta_fija=5s, forwards=30s, breakevens=30s),
    así que esta llamada NO multiplica trabajo: lee del cache de cada
    bloque y arma el dict.

    Pensado para que el frontend haga 1 poll cada 5s en vez de 3 polls
    paralelos (5/15/15s). Con 8 users concurrentes la carga al backend
    cae ~70% en esa pantalla.

    Nota: los timestamps individuales de cada bloque se pierden. El
    frontend muestra 1 solo "actualizado a las HH:MM:SS" para los 3
    paneles, que es el momento del fetch. Conceptualmente honesto:
    "estos 3 datos los recibí en este instante", aunque internamente
    forwards/breakevens vengan del cache.
    """
    return {
        "renta_fija": _rf(_engine).get_renta_fija(),
        "forwards":   _fwbe(_engine).get_forwards(),
        "breakevens": _fwbe(_engine).get_breakevens(),
    }


@router.get("/historico/breakevens")
def historico_breakevens(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    _engine: str | None = Query(None, include_in_schema=False),
):
    return _hist(_engine, svc_der).get_historico_breakevens(desde=desde, hasta=hasta)


# ── REM (Relevamiento de Expectativas de Mercado, BCRA) ──


@router.get("/rem/informes")
def rem_listar_informes(_engine: str | None = Query(None, include_in_schema=False)):
    """Informes REM disponibles (ordenados desc)."""
    return _rem(_engine).listar_informes()


@router.get("/rem")
def rem_expectativas(
    informe: str | None = Query(None, description="'YYYY-MM' o vacío=último disponible"),
    periodo_tipo: str | None = Query(None, description="mensual | anual | trimestral"),
    periodo_desde: str | None = Query(None, description="Filtro mínimo de periodo ('YYYY-MM')"),
    periodo_hasta: str | None = Query(None, description="Filtro máximo de periodo ('YYYY-MM')"),
    _engine: str | None = Query(None, include_in_schema=False),
):
    """Expectativas REM crudas (IPC nivel general INDEC): mediana / promedio /
    percentiles / participantes por período, ordenado asc."""
    return _rem(_engine).expectativas(
        informe=informe, periodo_tipo=periodo_tipo,
        periodo_desde=periodo_desde, periodo_hasta=periodo_hasta,
    )


@router.get("/rem/breakeven-acumulado")
def rem_breakeven_acumulado(
    informe: str | None = Query(None, description="'YYYY-MM' o vacío=último"),
    _engine: str | None = Query(None, include_in_schema=False),
):
    """IPC mensual del REM → promedio mensual geométrico acumulado desde HOY
    hasta cada mes futuro. Formato listo para superponer con breakeven de
    mercado en el chart."""
    return _rem(_engine).breakeven_acumulado(informe=informe)


@router.get("/rem/debug")
def rem_debug(_engine: str | None = Query(None, include_in_schema=False)):
    """Diagnóstico: qué informes / períodos hay y cuántos. Útil cuando el chart
    no dibuja."""
    return _rem(_engine).debug_info()


# ── Renta Fija ──


@router.get("/renta-fija")
def listar_renta_fija(
    instrumento: str | None = Query(None, description="Filtrar por instrumento. Acepta ticker corto ('TX26') o completo ('MERV - XMEV - TX26 - 24hs')."),
    _engine: str | None = Query(None, include_in_schema=False),
):
    return _rf(_engine).get_renta_fija(instrumento=instrumento)


# ── Opciones ──


@router.get("/opciones")
def listar_opciones(
    instrumento: str | None = Query(None, description="Filtrar por instrumento. Acepta corto ('GFGC10950A') o completo ('MERV - XMEV - GFGC10950A - 24hs')."),
    tipo: str | None = Query(None, description="Filtrar por tipo (CALL/PUT)"),
):
    return svc_opt.get_opciones(instrumento=instrumento, tipo=tipo)


@router.get("/opciones/meta")
def opciones_meta():
    return svc_opt.get_opciones_meta()


@router.put("/opciones/tasa")
def opciones_update_tasa(
    valor: float = Query(..., gt=0.0, lt=3.0, description="Tasa libre de riesgo (0.242 = 24.2%)"),
    _admin: str = Depends(require_module("manager")),
):
    """La tasa risk-free es global (afecta los Greeks de todos los users
    en simultáneo). Por eso solo el admin (módulo `manager`) puede
    modificarla — un trader o sales no debería poder cambiarle el shock
    a toda la mesa con un click."""
    try:
        return svc_opt.update_opciones_tasa(valor)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/historico/opciones")
def historico_opciones(
    instrumento: str | None = Query(None, description="Filtrar por instrumento (symbol)"),
    tipo: str | None = Query(None, description="Filtrar por tipo (CALL/PUT)"),
):
    return svc_opt.get_historico_opciones(instrumento=instrumento, tipo=tipo)


@router.get("/vr-ggal")
def vr_ggal():
    """Serie diaria GGAL local (ARS) + ADR (USD) de Opciones.VR-GGal.

    Para el 2º eje del chart de costo histórico (spot del subyacente).
    """
    return svc_opt.get_vr_ggal_serie()


@router.get("/griegas/opciones")
def griegas_opciones(
    instrumento: str = Query(..., description="Instrumento (symbol) del contrato"),
):
    """Evolución diaria de griegas de un contrato (Opciones.DataHistorica)."""
    return svc_opt.get_griegas_historico(instrumento=instrumento)


# ── Históricos TimeSales ──


@router.get("/historico/trades")
def historico_trades(
    instrumento: str | None = Query(None, description="Filtrar por instrumento. Acepta corto ('TX26') o completo ('MERV - XMEV - TX26 - 24hs')."),
    _engine: str | None = Query(None, include_in_schema=False),
):
    return _rf(_engine).get_historico_trades(instrumento=instrumento)


@router.get("/historico/curva")
def historico_curva(
    curva: str = Query(..., description="tasa_fija / cer"),
    _engine: str | None = Query(None, include_in_schema=False),
):
    return _rf(_engine).get_historico_curva(curva=curva)


