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
from fastapi import APIRouter, HTTPException, Query

from api.services import argy as svc_argy
from api.services import derivados as svc_der
from api.services import macro as svc_macro
from api.services import opciones as svc_opt
from api.services import rem as svc_rem
from api.services import renta_fija as svc_rf
from api.services import repo as svc_repo

router = APIRouter(prefix="/api/cotizaciones", tags=["Cotizaciones"])


# ── Series BCRA (BADLAR, CER, DOLAR) ──


@router.get("/badlar")
def listar_badlar(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    return svc_macro.get_badlar(desde=desde, hasta=hasta)


@router.get("/cer")
def listar_cer(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    return svc_macro.get_cer(desde=desde, hasta=hasta)


@router.get("/dolar")
def listar_dolar(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    return svc_macro.get_dolar(desde=desde, hasta=hasta)


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
):
    return svc_repo.get_historico_caucion(moneda=moneda, desde=desde, hasta=hasta)


# ── Futuros DLR ──


@router.get("/futuros-dlr")
def futuros_dlr():
    return svc_der.get_futuros_dlr()


@router.get("/historico/futuros-dlr")
def historico_futuros_dlr(
    ticker: str | None = Query(None, description="Filtrar por ticker (DLR/MMMYY)"),
    desde:  str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta:  str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    return svc_der.get_historico_futuros_dlr(ticker=ticker, desde=desde, hasta=hasta)


# ── Forwards ──


@router.get("/forwards")
def listar_forwards(
    curva: str | None = Query(None, description="Filtrar por curva (tasa_fija/cer)"),
):
    return svc_der.get_forwards(curva=curva)


@router.get("/historico/forwards")
def historico_forwards(
    curva: str | None = Query(None, description="Filtrar por curva (tasa_fija/cer)"),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    return svc_der.get_historico_forwards(curva=curva, desde=desde, hasta=hasta)


# ── Breakevens ──


@router.get("/breakevens")
def listar_breakevens():
    return svc_der.get_breakevens()


@router.get("/historico/breakevens")
def historico_breakevens(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    return svc_der.get_historico_breakevens(desde=desde, hasta=hasta)


# ── REM (Relevamiento de Expectativas de Mercado, BCRA) ──


@router.get("/rem/informes")
def rem_listar_informes():
    """Informes REM disponibles en Mongo (ordenados desc)."""
    return svc_rem.listar_informes()


@router.get("/rem")
def rem_expectativas(
    indicador: str | None = Query(None, description="Default: 'IPC nivel general'"),
    informe: str | None = Query(None, description="'YYYY-MM' o vacío=último disponible"),
    periodo_tipo: str | None = Query(None, description="mensual | anual | trimestral"),
    periodo_desde: str | None = Query(None, description="Filtro mínimo de periodo ('YYYY-MM')"),
    periodo_hasta: str | None = Query(None, description="Filtro máximo de periodo ('YYYY-MM')"),
):
    """Expectativas REM crudas por informe + indicador (mediana / promedio /
    percentiles / participantes, ordenado por periodo)."""
    return svc_rem.expectativas(
        indicador=indicador, informe=informe, periodo_tipo=periodo_tipo,
        periodo_desde=periodo_desde, periodo_hasta=periodo_hasta,
    )


@router.get("/rem/breakeven-acumulado")
def rem_breakeven_acumulado(
    informe: str | None = Query(None, description="'YYYY-MM' o vacío=último"),
    indicador: str | None = Query(None, description="Default: 'IPC nivel general'"),
):
    """IPC mensual del REM → promedio mensual geométrico acumulado desde HOY
    hasta cada mes futuro. Formato listo para superponer con breakeven de
    mercado en el chart."""
    return svc_rem.breakeven_acumulado(informe=informe, indicador=indicador)


# ── Renta Fija ──


@router.get("/renta-fija")
def listar_renta_fija(
    instrumento: str | None = Query(None, description="Filtrar por instrumento. Acepta ticker corto ('TX26') o completo ('MERV - XMEV - TX26 - 24hs')."),
):
    return svc_rf.get_renta_fija(instrumento=instrumento)


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
):
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


# ── Históricos TimeSales ──


@router.get("/historico/trades")
def historico_trades(
    instrumento: str | None = Query(None, description="Filtrar por instrumento. Acepta corto ('TX26') o completo ('MERV - XMEV - TX26 - 24hs')."),
):
    return svc_rf.get_historico_trades(instrumento=instrumento)


@router.get("/historico/curva")
def historico_curva(
    curva: str = Query(..., description="tasa_fija / cer"),
):
    return svc_rf.get_historico_curva(curva=curva)
