"""Router Analítica — Tier 1 + Tier 2 tools del asistente expuestas como HTTP.

Los 6 endpoints acá son thin wrappers sobre `api/services/*`. El asistente
los llama directo via service registry (sin HTTP loopback); este router
existe para consumo externo (acaquant-web, curl, debugging).
"""
from fastapi import APIRouter, Query

from api.services import analitica as svc_ana
from api.services import canje as svc_canje
from api.services import macro as svc_macro
from api.services import renta_fija as svc_rf
from api.services import sensibilidad as svc_sens

router = APIRouter(prefix="/api/analitica", tags=["Analítica"])


@router.get("/listar-curva")
def listar_curva(
    curva: str = Query(..., description="cer | tasa_fija | tamar | soberanos | dolar_linked"),
    ordenar_por: str = Query("vencimiento", description="vencimiento | volumen_dia | tea | duration"),
    vencimiento_min_meses: float | None = Query(None, description="Filtrar ≥ N meses"),
    vencimiento_max_meses: float | None = Query(None, description="Filtrar ≤ N meses"),
    limit: int | None = Query(None, description="Top N después de ordenar"),
):
    return svc_rf.listar_curva(
        curva=curva,
        ordenar_por=ordenar_por,
        vencimiento_min_meses=vencimiento_min_meses,
        vencimiento_max_meses=vencimiento_max_meses,
        limit=limit,
    )


@router.get("/serie-macro")
def serie_macro(
    variable: str = Query(..., description="tamar|cer|dolar|badlar|mep|ccl|canje|ipc|ipim|riesgo_pais|repo|rem_inflacion o <TICKER>.<CAMPO>"),
    ventana_dias: int = Query(90, ge=1, le=3650),
):
    return svc_macro.obtener_serie_macro(variable=variable, ventana_dias=ventana_dias)


@router.get("/clasificar-nivel")
def clasificar_nivel(
    variable: str = Query(..., description="Ver /serie-macro para valores válidos"),
    ventana_dias: int = Query(90, ge=1, le=3650),
):
    return svc_macro.clasificar_nivel(variable=variable, ventana_dias=ventana_dias)


# ── Tier 2: extensiones sobre data existente ──


@router.get("/snapshot-curva-historico")
def snapshot_curva_historico(
    curva: str = Query(..., description="cer|tasa_fija|tamar|soberanos|dolar_linked"),
    fecha: str = Query(..., description="YYYY-MM-DD (día de cierre a reconstruir)"),
):
    return svc_ana.snapshot_curva_historico(curva=curva, fecha=fecha)


@router.get("/pendiente-curva")
def pendiente_curva(
    curva: str = Query(..., description="cer|tasa_fija|tamar|soberanos"),
    metrica: str = Query("tea", description="tea|tem|duration"),
    fecha_comparacion: str | None = Query(None, description="YYYY-MM-DD opcional"),
):
    return svc_ana.calcular_pendiente_curva(
        curva=curva, metrica=metrica, fecha_comparacion=fecha_comparacion,
    )


@router.get("/liquidez-secundario")
def liquidez_secundario(
    ticker: str = Query(..., description="Ticker corto o completo ROFEX"),
    dias: int = Query(20, ge=3, le=252, description="Ventana para el promedio"),
):
    return svc_ana.liquidez_secundario(ticker=ticker, dias=dias)


@router.get("/sensibilidad-retorno")
def sensibilidad_retorno(
    curva: str = Query("soberanos", description="Curva (soberanos)"),
    tirs: str = Query("4,5,6,7,8,9,10,11",
                      description="CSV de TIRs (modo absoluta) o shocks pp "
                                  "(modo relativa). En %."),
    horizonte_dias: int = Query(365, ge=30, le=1095,
                                description="Horizonte en días (default 365)"),
    modo: str = Query("absoluta",
                      description="'absoluta' = TIRs finales; 'relativa' = "
                                  "shocks pp sobre la TEA actual"),
    tipos: str | None = Query(None,
                              description="CSV opcional para filtrar por "
                                          "tipo (ej. 'globales' o "
                                          "'globales,bonares'). Si se omite "
                                          "trae todos los tipos."),
):
    """Tabla de sensibilidad de retorno total por escenario de TIR.

    Para cada bono de la curva, devuelve precio actual + analíticos +
    [{tir, shock_pp, precio_1anio, retorno_total}, ...] por escenario.
    En modo relativa cada bono se evalúa con TIRs centradas en su TEA
    actual (comparación apples-to-apples).
    """
    try:
        tirs_t = tuple(float(t.strip()) / 100 for t in tirs.split(",") if t.strip())
    except ValueError:
        return {"error": "tirs malformado, esperado CSV de números"}
    if not tirs_t:
        return {"error": "tirs vacío"}
    tipos_t: tuple[str, ...] | None = None
    if tipos:
        tipos_t = tuple(t.strip() for t in tipos.split(",") if t.strip())
        if not tipos_t:
            tipos_t = None
    return svc_sens.sensibilidad_retorno_total(
        curva=curva, tirs=tirs_t, horizonte_dias=horizonte_dias,
        modo=modo, tipos=tipos_t,
    )


@router.get("/canje")
def canje(
    par: str = Query("AL30", description="Par (AL30 / GD30 / etc)"),
    desde: str | None = Query(None, description="YYYY-MM-DD (default: 365 días)"),
    hasta: str | None = Query(None, description="YYYY-MM-DD (default: hoy)"),
):
    """Serie histórica de canje legislación NY vs Arg.

    canje = precio_C / precio_D − 1. Devuelve serie diaria con precio
    de cada pata + el canje. Solo días con ambos precios disponibles.
    """
    return svc_canje.serie_canje(par=par, desde=desde, hasta=hasta)
