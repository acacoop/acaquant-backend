"""Router Analítica — Tier 1 + Tier 2 tools del asistente expuestas como HTTP.

Los 6 endpoints acá son thin wrappers sobre `api/services/*`. El asistente
los llama directo via service registry (sin HTTP loopback); este router
existe para consumo externo (acaquant-web, curl, debugging).
"""
from fastapi import APIRouter, Query

from api.services import analitica as svc_ana
from api.services import macro as svc_macro
from api.services import renta_fija as svc_rf

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
