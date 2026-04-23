"""Mapping de endpoint HTTP → función de servicio (Python puro).

Cuando el dispatch del agente tiene que ejecutar una tool, mira este registry
primero. Si hay una función registrada para el endpoint, la invoca
**directamente** (sin loopback HTTP). Si no, fallback a `requests.get` como
antes.

Ganancia:
- ~100-300ms por turn (sin DNS loopback + socket + re-parse FastAPI + verify
  auth + re-serialización JSON).
- Tests del agente sin levantar uvicorn.
- Cache compartido entre router y dispatch (mismo `@cached` en el service).

Cómo agregar una tool nueva:
1. Escribir la función en `api/services/<modulo>.py` con kwargs opcionales que
   matcheen los args que declara el TOOLS entry en `tools.py`.
2. Agregar entry `{"/api/<ruta>": servicio.get_xxx}` acá.

IMPORTANTE: la función del service debe tolerar kwargs desconocidos con
default `None`, porque los LLMs a veces envían params extra. Si querés
rechazar kwargs inesperados, hacelo explícito dentro del service.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from api.services import analitica as svc_ana
from api.services import argy as svc_argy
from api.services import canje as svc_canje
from api.services import carry_trade as svc_carry
from api.services import derivados as svc_der
from api.services import macro as svc_macro
from api.services import opciones as svc_opt
from api.services import rem as svc_rem
from api.services import renta_fija as svc_rf
from api.services import repo as svc_repo
from api.services import sensibilidad as svc_sens


def _sensibilidad_retorno_csv(
    curva: str = "soberanos",
    tirs: str = "4,5,6,7,8,9,10,11",
    horizonte_dias: int = 0,
    modo: str = "absoluta",
    tipos: str | None = None,
) -> Any:
    """Wrapper que parsea `tirs` y `tipos` como CSV (mismo contrato que el
    endpoint HTTP). El service interno espera tuplas de floats; el modelo nos
    pasa strings — acá hacemos la traducción para evitar duplicar lógica del
    router en el dispatcher."""
    try:
        tirs_t = tuple(
            float(t.strip()) / 100 for t in str(tirs).split(",") if t.strip()
        )
    except ValueError:
        return {"error": "tirs malformado, esperado CSV de números (ej '6,8,10')"}
    if not tirs_t:
        return {"error": "tirs vacío"}
    tipos_t: tuple[str, ...] | None = None
    if tipos:
        tipos_t = tuple(t.strip() for t in str(tipos).split(",") if t.strip()) or None
    return svc_sens.sensibilidad_retorno_total(
        curva=curva, tirs=tirs_t, horizonte_dias=horizonte_dias,
        modo=modo, tipos=tipos_t,
    )

# Endpoint → función servicio. La función recibe kwargs (mismo shape que
# los args del TOOLS entry) y devuelve lo que el endpoint HTTP devolvería.
SERVICE_HANDLERS: dict[str, Callable[..., Any]] = {
    # Analítica — Tier 1 (framework stats + curva general)
    "/api/analitica/listar-curva":        svc_rf.listar_curva,
    "/api/analitica/serie-macro":         svc_macro.obtener_serie_macro,
    "/api/analitica/clasificar-nivel":    svc_macro.clasificar_nivel,

    # Analítica — Tier 2 (extensiones sobre TimeSales y MarketSnapshot)
    "/api/analitica/snapshot-curva-historico": svc_ana.snapshot_curva_historico,
    "/api/analitica/pendiente-curva":          svc_ana.calcular_pendiente_curva,
    "/api/analitica/liquidez-secundario":      svc_ana.liquidez_secundario,

    # Analítica — Tier 3 (estrategia: canje, carry, sensibilidad de retorno)
    "/api/analitica/canje":                svc_canje.serie_canje,
    "/api/analitica/carry-trade":          svc_carry.serie_carry_trade,
    "/api/analitica/sensibilidad-retorno": _sensibilidad_retorno_csv,

    # Cotizaciones — series BCRA
    "/api/cotizaciones/badlar":     svc_macro.get_badlar,
    "/api/cotizaciones/cer":        svc_macro.get_cer,
    "/api/cotizaciones/dolar":      svc_macro.get_dolar,

    # Cotizaciones — MEP
    "/api/cotizaciones/mep":            svc_macro.get_ultimo_mep,
    "/api/cotizaciones/historico/mep":  svc_macro.get_historico_mep,

    # Cotizaciones — Caución (repo market, live + cierre histórico)
    "/api/cotizaciones/caucion":            svc_repo.get_caucion,
    "/api/cotizaciones/historico/caucion":  svc_repo.get_historico_caucion,

    # Cotizaciones — ARGY (snapshot multi-métrica con returns)
    "/api/cotizaciones/argy":               svc_argy.get_argy_with_returns,

    # Cotizaciones — Futuros DLR (curva live + cierre histórico)
    "/api/cotizaciones/futuros-dlr":            svc_der.get_futuros_dlr,
    "/api/cotizaciones/historico/futuros-dlr":  svc_der.get_historico_futuros_dlr,

    # Cotizaciones — Forwards
    "/api/cotizaciones/forwards":           svc_der.get_forwards,
    "/api/cotizaciones/historico/forwards": svc_der.get_historico_forwards,

    # Cotizaciones — Breakevens
    "/api/cotizaciones/breakevens":           svc_der.get_breakevens,
    "/api/cotizaciones/historico/breakevens": svc_der.get_historico_breakevens,

    # Cotizaciones — REM (expectativas BCRA)
    "/api/cotizaciones/rem":                    svc_rem.expectativas,
    "/api/cotizaciones/rem/informes":           svc_rem.listar_informes,
    "/api/cotizaciones/rem/breakeven-acumulado": svc_rem.breakeven_acumulado,

    # Cotizaciones — Renta fija / opciones
    "/api/cotizaciones/renta-fija":    svc_rf.get_renta_fija,
    "/api/cotizaciones/opciones":      svc_opt.get_opciones,
    "/api/cotizaciones/opciones/meta": svc_opt.get_opciones_meta,

    # Cotizaciones — históricos TimeSales
    "/api/cotizaciones/historico/trades":    svc_rf.get_historico_trades,
    "/api/cotizaciones/historico/curva":     svc_rf.get_historico_curva,
    "/api/cotizaciones/historico/opciones":  svc_opt.get_historico_opciones,
}


def get_service_handler(endpoint: str) -> Callable[..., Any] | None:
    """Devuelve la función de servicio para un endpoint, o None si no hay."""
    return SERVICE_HANDLERS.get(endpoint)
