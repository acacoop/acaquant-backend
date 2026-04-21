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

from api.services import argy as svc_argy
from api.services import cotizaciones as svc_cotizaciones
from api.services import macro as svc_macro

# Endpoint → función servicio. La función recibe kwargs (mismo shape que
# los args del TOOLS entry) y devuelve lo que el endpoint HTTP devolvería.
SERVICE_HANDLERS: dict[str, Callable[..., Any]] = {
    # Analítica — Tier 1 (framework stats + curva general)
    "/api/analitica/listar-curva":        svc_cotizaciones.listar_curva,
    "/api/analitica/serie-macro":         svc_macro.obtener_serie_macro,
    "/api/analitica/clasificar-nivel":    svc_macro.clasificar_nivel,

    # Analítica — Tier 2 (extensiones sobre TimeSales y MarketSnapshot)
    "/api/analitica/snapshot-curva-historico": svc_cotizaciones.snapshot_curva_historico,
    "/api/analitica/pendiente-curva":          svc_cotizaciones.calcular_pendiente_curva,
    "/api/analitica/liquidez-secundario":      svc_cotizaciones.liquidez_secundario,

    # Cotizaciones — series BCRA
    "/api/cotizaciones/badlar":     svc_cotizaciones.get_badlar,
    "/api/cotizaciones/cer":        svc_cotizaciones.get_cer,
    "/api/cotizaciones/dolar":      svc_cotizaciones.get_dolar,

    # Cotizaciones — MEP
    "/api/cotizaciones/mep":            svc_cotizaciones.get_ultimo_mep,
    "/api/cotizaciones/historico/mep":  svc_cotizaciones.get_historico_mep,

    # Cotizaciones — Caución (live + cierre histórico)
    "/api/cotizaciones/caucion":            svc_cotizaciones.get_caucion,
    "/api/cotizaciones/historico/caucion":  svc_cotizaciones.get_historico_caucion,

    # Cotizaciones — ARGY (snapshot multi-métrica con returns)
    "/api/cotizaciones/argy":               svc_argy.get_argy_with_returns,

    # Cotizaciones — Futuros DLR (curva live + cierre histórico)
    "/api/cotizaciones/futuros-dlr":            svc_cotizaciones.get_futuros_dlr,
    "/api/cotizaciones/historico/futuros-dlr":  svc_cotizaciones.get_historico_futuros_dlr,

    # Cotizaciones — Forwards
    "/api/cotizaciones/forwards":           svc_cotizaciones.get_forwards,
    "/api/cotizaciones/historico/forwards": svc_cotizaciones.get_historico_forwards,

    # Cotizaciones — Breakevens
    "/api/cotizaciones/breakevens":           svc_cotizaciones.get_breakevens,
    "/api/cotizaciones/historico/breakevens": svc_cotizaciones.get_historico_breakevens,

    # Cotizaciones — Renta fija / opciones
    "/api/cotizaciones/renta-fija":    svc_cotizaciones.get_renta_fija,
    "/api/cotizaciones/opciones":      svc_cotizaciones.get_opciones,
    "/api/cotizaciones/opciones/meta": svc_cotizaciones.get_opciones_meta,

    # Cotizaciones — históricos TimeSales
    "/api/cotizaciones/historico/trades":    svc_cotizaciones.get_historico_trades,
    "/api/cotizaciones/historico/curva":     svc_cotizaciones.get_historico_curva,
    "/api/cotizaciones/historico/opciones":  svc_cotizaciones.get_historico_opciones,
}


def get_service_handler(endpoint: str) -> Callable[..., Any] | None:
    """Devuelve la función de servicio para un endpoint, o None si no hay."""
    return SERVICE_HANDLERS.get(endpoint)
