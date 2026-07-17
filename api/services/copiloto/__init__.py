"""api/services/copiloto — P3 Copiloto de Mesa (QuantAI, docs/QUANTAI.md).

⚠️ TRAZABILIDAD OBLIGATORIA: todo cambio a lo que el asistente ve o cómo se
comporta (VISTAS, columnas, reglas, prompt, detección) se asienta CON FECHA en
el changelog de docs/COPILOTO.md, en el MISMO commit. Sin eso, incompleto.

Copiloto CONTEXTUAL por vista de mercado: el usuario pregunta desde una tabla de
la app y la IA responde SOLO con los datos de ESA tabla. No es un agente: una
llamada al LLM por pregunta, contexto armado server-side desde el service
@cached de la vista (workflow, no agente).

Modularizado 2026-07-17 (era un único copiloto.py de ~3.400 líneas). Mapa:
- base           helpers puros + system prompt + tono por rol
- verificacion   guardrails de números/jerga/derrame (anti-alucinación)
- <vista>.py     una por vista (renta_variable, renta_fija, trading, home,
                 agro, opciones, ons, reuters): fetch + extras + reglas + chips
- registro       el dict VISTAS (ensambla las vistas)
- derivacion     [[VISTA:x]] + acceso RBAC (vistas_para / puede_usar)
- motor          preguntar() + vigia() + historial + feedback

Este __init__ re-exporta la superficie pública histórica para que
`from api.services import copiloto; copiloto.X` siga funcionando igual.
"""
from __future__ import annotations

from .agro import _fetch_agro
from .base import (
    _MAX_FILAS,
    _MAX_TICKERS_DETALLE,
    _celda,
    _detectar_tickers,
    _pct,
    _tsv,
    _zona,
)
from .derivacion import _extraer_vista_sugerida, puede_usar, vistas_para
from .home import _briefing_bloque, _fetch_home, _futuros_dlr_bloque, _renta_fija_pulso
from .motor import (
    historial_persistido,
    preguntar,
    registrar_feedback,
    vigia,
)
from .ons import _extras_ons, _fetch_ons
from .opciones import _fetch_opciones
from .registro import VISTAS
from .renta_fija import _estrategia_rf, _fetch_renta_fija, _rem_promedio_hasta, _resumen_curvas_rf
from .renta_variable import (
    _enriquecer_cedears,
    _pulso_por_rubro,
    _rankings,
    _screenings,
)
from .trading import _estado_mercado, _fetch_trading, _sanear_params_trading, _sanear_posiciones
from .verificacion import _candidatos_numericos, _jerga_en_respuesta, _numeros_sin_respaldo

__all__ = [
    "VISTAS",
    "_MAX_FILAS",
    "_MAX_TICKERS_DETALLE",
    "_briefing_bloque",
    "_candidatos_numericos",
    "_celda",
    "_detectar_tickers",
    "_enriquecer_cedears",
    "_estado_mercado",
    "_estrategia_rf",
    "_extraer_vista_sugerida",
    "_extras_ons",
    "_fetch_agro",
    "_fetch_home",
    "_fetch_ons",
    "_fetch_opciones",
    "_fetch_renta_fija",
    "_fetch_trading",
    "_futuros_dlr_bloque",
    "_jerga_en_respuesta",
    "_numeros_sin_respaldo",
    "_pct",
    "_pulso_por_rubro",
    "_rankings",
    "_rem_promedio_hasta",
    "_renta_fija_pulso",
    "_resumen_curvas_rf",
    "_sanear_params_trading",
    "_sanear_posiciones",
    "_screenings",
    "_tsv",
    "_zona",
    "historial_persistido",
    "preguntar",
    "puede_usar",
    "registrar_feedback",
    "vigia",
    "vistas_para",
]

