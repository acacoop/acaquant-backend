"""Router Cotizaciones — thin wrappers sobre api.services.cotizaciones.

La lógica vive en `api/services/cotizaciones.py`. Acá solo declaramos
endpoints FastAPI que parsean query params y delegan al service.

Motivo: la misma capa de servicio la usa `api/agent/tools.py::dispatch` sin
pagar el roundtrip HTTP loopback. Ver auditoría #14.
"""
from fastapi import APIRouter, HTTPException, Query

from api.services import argy as svc_argy
from api.services import cotizaciones as svc

router = APIRouter(prefix="/api/cotizaciones", tags=["Cotizaciones"])


# ── Series BCRA (BADLAR, CER, DOLAR) ──


@router.get("/badlar")
def listar_badlar(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    return svc.get_badlar(desde=desde, hasta=hasta)


@router.get("/cer")
def listar_cer(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    return svc.get_cer(desde=desde, hasta=hasta)


@router.get("/dolar")
def listar_dolar(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    return svc.get_dolar(desde=desde, hasta=hasta)


# ── Dólar MEP ──


@router.get("/mep")
def ultimo_mep():
    return svc.get_ultimo_mep()


@router.get("/historico/mep")
def historico_mep(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    return svc.get_historico_mep(desde=desde, hasta=hasta)


# ── ARGY (panel de control: MEP/CCL/canje/cauciones con returns) ──


@router.get("/argy")
def argy():
    return svc_argy.get_argy_with_returns()


# ── Caución ──


@router.get("/caucion")
def caucion(
    moneda: str | None = Query(None, description="ARS o USD; vacío = ambas"),
):
    return svc.get_caucion(moneda=moneda)


@router.get("/historico/caucion")
def historico_caucion(
    moneda: str | None = Query(None, description="ARS o USD"),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    return svc.get_historico_caucion(moneda=moneda, desde=desde, hasta=hasta)


# ── Futuros DLR ──


@router.get("/futuros-dlr")
def futuros_dlr():
    return svc.get_futuros_dlr()


@router.get("/historico/futuros-dlr")
def historico_futuros_dlr(
    ticker: str | None = Query(None, description="Filtrar por ticker (DLR/MMMYY)"),
    desde:  str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta:  str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    return svc.get_historico_futuros_dlr(ticker=ticker, desde=desde, hasta=hasta)


# ── Forwards ──


@router.get("/forwards")
def listar_forwards(
    curva: str | None = Query(None, description="Filtrar por curva (tasa_fija/cer)"),
):
    return svc.get_forwards(curva=curva)


@router.get("/historico/forwards")
def historico_forwards(
    curva: str | None = Query(None, description="Filtrar por curva (tasa_fija/cer)"),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    return svc.get_historico_forwards(curva=curva, desde=desde, hasta=hasta)


# ── Breakevens ──


@router.get("/breakevens")
def listar_breakevens():
    return svc.get_breakevens()


@router.get("/historico/breakevens")
def historico_breakevens(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    return svc.get_historico_breakevens(desde=desde, hasta=hasta)


# ── Renta Fija ──


@router.get("/renta-fija")
def listar_renta_fija(
    instrumento: str | None = Query(None, description="Filtrar por instrumento. Acepta ticker corto ('TX26') o completo ('MERV - XMEV - TX26 - 24hs')."),
):
    return svc.get_renta_fija(instrumento=instrumento)


# ── Opciones ──


@router.get("/opciones")
def listar_opciones(
    instrumento: str | None = Query(None, description="Filtrar por instrumento. Acepta corto ('GFGC10950A') o completo ('MERV - XMEV - GFGC10950A - 24hs')."),
    tipo: str | None = Query(None, description="Filtrar por tipo (CALL/PUT)"),
):
    return svc.get_opciones(instrumento=instrumento, tipo=tipo)


@router.get("/opciones/meta")
def opciones_meta():
    return svc.get_opciones_meta()


@router.put("/opciones/tasa")
def opciones_update_tasa(
    valor: float = Query(..., gt=0.0, lt=3.0, description="Tasa libre de riesgo (0.242 = 24.2%)"),
):
    try:
        return svc.update_opciones_tasa(valor)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/historico/opciones")
def historico_opciones(
    instrumento: str | None = Query(None, description="Filtrar por instrumento (symbol)"),
    tipo: str | None = Query(None, description="Filtrar por tipo (CALL/PUT)"),
):
    return svc.get_historico_opciones(instrumento=instrumento, tipo=tipo)


# ── Históricos TimeSales ──


@router.get("/historico/trades")
def historico_trades(
    instrumento: str | None = Query(None, description="Filtrar por instrumento. Acepta corto ('TX26') o completo ('MERV - XMEV - TX26 - 24hs')."),
):
    return svc.get_historico_trades(instrumento=instrumento)


@router.get("/historico/curva")
def historico_curva(
    curva: str = Query(..., description="tasa_fija / cer"),
):
    return svc.get_historico_curva(curva=curva)
