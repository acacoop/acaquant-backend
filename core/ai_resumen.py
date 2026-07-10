"""core/ai_resumen.py — lectura ejecutiva con IA para los informes operativos.

Acá vive el PROMPT de la tarea "controles_resumen" (la lectura ejecutiva del
auto-control de datos, jobs/controles_datos) y el capado de su payload. El
transporte (proveedor, modelo, timeout, reintentos, presupuesto, traza) lo
resuelve el gateway core/ai.py — este módulo NO habla con el proveedor.

Contrato intacto para el caller: resumen_ejecutivo() devuelve str o None (sin
DEEPSEEK_API_KEY o ante cualquier fallo → None y el caller usa su render
determinista). NUNCA propaga excepción.

Regla del canal: al prompt solo entra metadata operativa (conteos, tickers,
nombres de jobs) — el caller es responsable de NO pasar datos de clientes.

Env vars (las lee el gateway): DEEPSEEK_API_KEY; AI_RESUMEN_MODEL (override
del modelo solo para esta tarea).
"""
from __future__ import annotations

import json
import logging

from core.ai import completar

logger = logging.getLogger(__name__)

_MAX_PAYLOAD_CHARS = 12_000  # techo del contexto que mandamos (json del informe)

_SYSTEM = (
    "Sos el analista de operaciones de ACAquant, una mesa de trading argentina "
    "(MERVAL/ROFEX). Recibís el resultado JSON del control diario de calidad de "
    "datos y fallas de procesos, y escribís la lectura ejecutiva para el dueño "
    "de la mesa (PM, no técnico), en español rioplatense.\n"
    "Reglas:\n"
    "- Máximo 6 oraciones. Priorizá: 1º lo NUEVO de hoy, 2º lo que sigue roto "
    "hace días (mirá dias_mas_viejo), 3º lo resuelto. Si no hay nada nuevo ni "
    "roto, decilo en una línea y listo.\n"
    "- Explicá el IMPACTO de negocio de cada problema (qué vista/dato queda mal), "
    "no el síntoma técnico.\n"
    "- Si hay una acción concreta (completar un campo en Manager, dar de alta una "
    "cuenta, revisar un motor), decila.\n"
    "- No inventes nada que no esté en el JSON. No repitas números que ya están "
    "en el mensaje determinista salvo que ayuden a priorizar.\n"
    "- Texto plano, sin markdown, sin encabezados."
)


def resumen_ejecutivo(payload: dict) -> str | None:
    """Párrafo ejecutivo sobre el resultado de los controles. None si la capa AI
    no está disponible (sin key) o falla — el caller renderiza sin ella."""
    try:
        contexto = json.dumps(payload, ensure_ascii=False, default=str)[:_MAX_PAYLOAD_CHARS]
    except Exception as e:
        logger.warning("ai_resumen: payload no serializable: %s", e)
        return None
    return completar("controles_resumen", system=_SYSTEM, user=contexto)
