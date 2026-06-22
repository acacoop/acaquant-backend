"""Router Analítica — Tier 1 + Tier 2 tools del asistente expuestas como HTTP.

Los 6 endpoints acá son thin wrappers sobre `api/services/*`. El asistente
los llama directo via service registry (sin HTTP loopback); este router
existe para consumo externo (acaquant-web, curl, debugging).
"""
import os

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel, Field

from api.services import analitica as svc_ana
from api.services import canje as svc_canje
from api.services import carry_trade as svc_carry
from api.services import comparar_inversion as svc_cmp
from api.services import descomposicion_retorno as svc_desc
from api.services import macro as svc_macro
from api.services import macro_sql as svc_macro_sql
from api.services import opciones as svc_opc
from api.services import renta_fija as svc_rf
from api.services import renta_fija_sql as svc_rf_sql
from api.services import sensibilidad as svc_sens

router = APIRouter(prefix="/api/analitica", tags=["Analítica"])


def _macro(engine: str | None):
    """Selector de motor de series macro (mismo criterio que cotizaciones._macro):
    SQL si `?_engine=sql` o flag `MACRO_SQL=1`; default Mongo. El path SQL delega
    a Mongo lo no-migrado (mep/ccl/canje, series por ticker, caución)."""
    use_sql = engine == "sql" or (engine != "mongo" and os.getenv("MACRO_SQL") == "1")
    return svc_macro_sql if use_sql else svc_macro


def _rf(engine: str | None):
    """Selector de renta fija LIVE (mismo criterio que cotizaciones._rf): SQL
    (mercado.*) si `?_engine=sql` o flag `RENTA_FIJA_SQL=1`; Mongo en otro caso."""
    use_sql = engine == "sql" or (engine != "mongo" and os.getenv("RENTA_FIJA_SQL") == "1")
    return svc_rf_sql if use_sql else svc_rf


@router.get("/listar-curva")
def listar_curva(
    curva: str = Query(..., description="cer | tasa_fija | tamar | soberanos | dolar_linked | on | on_<sector>"),
    ordenar_por: str = Query("vencimiento", description="vencimiento | volumen_dia | tea | duration"),
    vencimiento_min_meses: float | None = Query(None, description="Filtrar ≥ N meses"),
    vencimiento_max_meses: float | None = Query(None, description="Filtrar ≤ N meses"),
    limit: int | None = Query(None, description="Top N después de ordenar"),
    _engine: str | None = Query(None, include_in_schema=False),
):
    return _rf(_engine).listar_curva(
        curva=curva,
        ordenar_por=ordenar_por,
        vencimiento_min_meses=vencimiento_min_meses,
        vencimiento_max_meses=vencimiento_max_meses,
        limit=limit,
    )


@router.get("/ons-calendario")
def ons_calendario(
    meses: int = Query(12, description="Horizonte en meses (1-120)"),
):
    """Calendario de pagos de las ONs: próximos cupones/amortizaciones."""
    return svc_rf.calendario_ons(meses=meses)


@router.get("/serie-macro")
def serie_macro(
    variable: str = Query(..., description="tamar|cer|dolar|badlar|mep|ccl|canje|ipc|ipim|riesgo_pais|repo|rem_inflacion o <TICKER>.<CAMPO>"),
    ventana_dias: int = Query(90, ge=1, le=3650),
    _engine: str | None = Query(None, include_in_schema=False),
):
    return _macro(_engine).obtener_serie_macro(variable=variable, ventana_dias=ventana_dias)


@router.get("/clasificar-nivel")
def clasificar_nivel(
    variable: str = Query(..., description="Ver /serie-macro para valores válidos"),
    ventana_dias: int = Query(90, ge=1, le=3650),
    _engine: str | None = Query(None, include_in_schema=False),
):
    return _macro(_engine).clasificar_nivel(variable=variable, ventana_dias=ventana_dias)


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
    dias_min_corto: int = Query(30, ge=0, le=365,
                                description="Excluye bonos del anchor 'corto' "
                                            "con menos de N días al vencimiento. "
                                            "Default 30 (evita ruido de fin de plazo)."),
):
    return svc_ana.calcular_pendiente_curva(
        curva=curva, metrica=metrica,
        fecha_comparacion=fecha_comparacion,
        dias_min_corto=dias_min_corto,
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
    horizonte_dias: int = Query(0, ge=0, le=1095,
                                description="Días a proyectar el precio. "
                                            "0 = upside instantáneo; 365 = "
                                            "dentro de 1 año (incluye "
                                            "pull-to-par, sin carry)."),
    modo: str = Query("absoluta",
                      description="'absoluta' = TIRs finales; 'relativa' = "
                                  "shocks pp sobre la TEA actual"),
    tipos: str | None = Query(None,
                              description="CSV opcional para filtrar por "
                                          "tipo (ej. 'globales' o "
                                          "'globales,bonares'). Si se omite "
                                          "trae todos los tipos."),
):
    """Tabla de sensibilidad de PRECIO por escenario de TIR (upside capital-only).

    Responde: si dentro de `horizonte_dias` días el bono cotiza a TIR X,
    ¿a qué precio estaría y cuánto es el upside vs el precio actual? NO
    incluye carry — es capital puro. Con horizonte=0 es instantáneo;
    horizontes mayores incluyen pull-to-par.

    Para cada bono devuelve precio actual + analíticos +
    [{tir, shock_pp, precio_objetivo, upside}, ...] por escenario.
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
    """Serie histórica del canje CCL/MEP intra-bono.

    Mismo bono, dos especies (C y D). canje = precio_C / precio_D − 1.
    Devuelve serie diaria con precio de cada pata + el canje. Solo días
    con ambos precios disponibles. NO es "spread legislación" (GD30 vs
    AL30) — es la brecha CCL/MEP implícita en un único bono.
    """
    return svc_canje.serie_canje(par=par, desde=desde, hasta=hasta)


@router.get("/carry-trade")
def carry_trade(
    curva: str = Query("tasa_fija", description="tasa_fija | cer"),
    desde: str | None = Query(None, description="YYYY-MM-DD (default: 180 días)"),
    hasta: str | None = Query(None, description="YYYY-MM-DD (default: hoy)"),
    dolar: str = Query("mep", description="mep | ccl"),
):
    """Carry trade en USD por bono = retorno ARS descontado por var del dólar.

    Fórmula: (1 + ret_ars) / (1 + var_dolar) − 1.
    """
    return svc_carry.serie_carry_trade(
        curva=curva, desde=desde, hasta=hasta, dolar=dolar,
    )


@router.get("/retorno-total")
def retorno_total(
    curva: str = Query(..., description="cer | tasa_fija | soberanos"),
):
    """Datos consolidados para la vista Retorno Total.

    Precios diarios de la curva + series MEP/oficial (para dolarizar
    curvas en pesos). El frontend calcula retornos ARS y USD con
    carry-forward. Para `soberanos` los precios ya están en USD.
    """
    return svc_rf.get_retorno_total_data(curva=curva)


class _EstrategiaLeg(BaseModel):
    offset: int = 0
    tipo: str  # CALL | PUT
    side: str  # buy | sell
    qty: int = 1


class _EstrategiaHistoricoReq(BaseModel):
    legs: list[_EstrategiaLeg] = Field(..., min_length=1, max_length=8)
    bucket_min: int = Field(15, ge=1, le=240)
    desde: str | None = None
    hasta: str | None = None


@router.get("/descomposicion-retorno")
def descomposicion_retorno(
    desde: str = Query(..., description="YYYY-MM-DD (snapshot inicial)"),
    hasta: str = Query(..., description="YYYY-MM-DD (snapshot final, > desde)"),
    metodo: str = Query("lineal", description="lineal | cuadratica"),
    curva: str = Query("tasa_fija", description="tasa_fija | cer"),
):
    """Atribución ex-post entre dos fechas. Curva: tasa_fija (default) o cer.

    Descompone el retorno total en 3 componentes puros: carry (paso del
    tiempo), rolldown (rolling sin que se mueva la curva) y cambio_tasa
    (residuo, lo que el mercado movió). Cada uno en %.

    Para `curva=cer` el cálculo se hace sobre paridad + TEA real (no sobre
    precio sucio para no doble-contar la indexación) y agrega `cer_accrual`
    del período + `r_total_ars` compuesto. Carry y descomposición usan
    forma exacta (composición exponencial), no linealización.
    """
    return svc_desc.descomposicion_realizada(
        desde=desde, hasta=hasta, metodo=metodo, curva=curva,
    )


@router.get("/rolldown-esperado")
def rolldown_esperado(
    horizonte_dias: int = Query(30, ge=1, le=365, description="Horizonte en días"),
    metodo: str = Query("lineal", description="lineal | cuadratica"),
    curva: str = Query("tasa_fija", description="tasa_fija | cer"),
):
    """Atribución prospectiva: retorno esperado a horizonte si la curva no
    se mueve. Curva: tasa_fija (default) o cer.

    Para `curva=cer` suma `cer_accrual_esperado` (mediana del REM proyectada
    en N meses) y `total_esperado_ars` compuesto. Útil para rankear qué
    Lecap/Lecer comprar bajo escenario de curva quieta.
    """
    return svc_desc.rolldown_esperado(
        horizonte_dias=horizonte_dias, metodo=metodo, curva=curva,
    )


@router.post("/estrategia-historico")
def estrategia_historico(req: _EstrategiaHistoricoReq = Body(...)):
    """Serie intradía de costo de una estrategia de opciones.

    Lee Opciones.Data (tick-level del OPEX en curso), agrupa en buckets de
    N minutos, reproduce en cada bucket el cálculo de costo del frontend:
    construye la chain por strike, detecta el ATM del bucket (strike líquido
    más cercano al spot), aplica los offsets de cada pata del template, y
    suma precios (buy=offer, sell=bid, fallback last) × qty × 100.

    Buckets donde la estrategia no es válida (pata fuera de rango o iliquida)
    se omiten — la serie queda con huecos, no con ceros.
    """
    legs = [leg.model_dump() for leg in req.legs]
    return svc_opc.estrategia_historico(
        legs=legs,
        bucket_min=req.bucket_min,
        desde=req.desde,
        hasta=req.hasta,
    )


@router.get("/comparar/bonos")
def comparar_listar_bonos():
    """Universo para el selector de la tab 'Comparar Inversión'.

    Fase 1: solo Trading.Curvas (cer, tasa_fija, soberanos). BondsMaster
    pendiente para Fase 2.
    """
    return svc_cmp.listar_bonos_seleccionables()


@router.get("/comparar")
def comparar_inversion(
    a: str = Query(..., description="id del bono A (ej. 'curvas:TX26')"),
    b: str = Query(..., description="id del bono B"),
    monto: float = Query(..., gt=0, description="Monto a invertir"),
    moneda: str = Query("ARS", description="Moneda del monto: ARS o USD"),
):
    return svc_cmp.comparar(a_id=a, b_id=b, monto=monto, moneda_input=moneda)
