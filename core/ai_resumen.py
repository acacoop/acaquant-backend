"""core/ai_resumen.py — lectura ejecutiva con Claude para los informes operativos.

Toma el resultado estructurado de un control/informe (dict) y devuelve un párrafo
ejecutivo en criollo: qué pasó, qué es nuevo, qué mirar primero. Es la capa "AI"
del auto-control de datos (jobs/controles_datos).

100% opcional y best-effort: si falta ANTHROPIC_API_KEY, no está instalado el
paquete `anthropic`, o la API falla, devuelve None y el caller usa su render
determinista. NUNCA propaga excepción (mismo contrato que core/notify.py).

Regla del canal: al prompt solo entra metadata operativa (conteos, tickers,
nombres de jobs) — el caller es responsable de NO pasar datos de clientes.

Env vars:
  ANTHROPIC_API_KEY   — requerida para activar la capa AI (sin ella → None).
  AI_RESUMEN_MODEL    — override del modelo (default claude-opus-4-8).
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

_TIMEOUT_S = 60
_MAX_TOKENS = 800
_MAX_PAYLOAD_CHARS = 12_000  # techo del contexto que mandamos (json del informe)

_SYSTEM = (
    "Sos el analista de operaciones de ACAquant, una mesa de trading argentina "
    "(MERVAL/ROFEX). Recibís el resultado JSON del control diario de calidad de "
    "datos y fallas de procesos, y escribís la lectura ejecutiva para el dueño "
    "de la mesa (PM, no técnico), en español rioplatense.\n"
    "Reglas:\n"
    "- Máximo 6 oraciones. Priorizá: 1º lo NUEVO de hoy, 2º lo que sigue roto "
    "hace días (mirá first_seen), 3º lo resuelto. Si no hay nada nuevo ni roto, "
    "decilo en una línea y listo.\n"
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
    no está disponible (sin key/paquete) o falla — el caller renderiza sin ella."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        logger.info("ai_resumen: paquete `anthropic` no instalado — sin lectura AI")
        return None
    try:
        contexto = json.dumps(payload, ensure_ascii=False, default=str)[:_MAX_PAYLOAD_CHARS]
        client = anthropic.Anthropic(timeout=_TIMEOUT_S, max_retries=1)
        resp = client.messages.create(
            model=os.getenv("AI_RESUMEN_MODEL", "claude-opus-4-8"),
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM,
            messages=[{"role": "user", "content": contexto}],
        )
        texto = "".join(b.text for b in resp.content if b.type == "text").strip()
        return texto or None
    except Exception as e:
        logger.warning("ai_resumen falló: %s: %s", type(e).__name__, e)
        return None
