"""core/ai_resumen.py — lectura ejecutiva con IA para los informes operativos.

Toma el resultado estructurado de un control/informe (dict) y devuelve un párrafo
ejecutivo en criollo: qué pasó, qué es nuevo, qué mirar primero. Es la capa "AI"
del auto-control de datos (jobs/controles_datos).

Proveedor: DeepSeek (API OpenAI-compatible, https://api.deepseek.com) — elegido
por costo: el resumen diario son ~3-6k tokens de input y <1k de output, y
deepseek-chat lo resuelve bien por centavos al mes.

100% opcional y best-effort: si falta DEEPSEEK_API_KEY o la API falla, devuelve
None y el caller usa su render determinista. NUNCA propaga excepción (mismo
contrato que core/notify.py).

Regla del canal: al prompt solo entra metadata operativa (conteos, tickers,
nombres de jobs) — el caller es responsable de NO pasar datos de clientes.

Env vars:
  DEEPSEEK_API_KEY  — requerida para activar la capa AI (sin ella → None).
  AI_RESUMEN_MODEL  — override del modelo (default deepseek-chat).
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

_URL = "https://api.deepseek.com/chat/completions"
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
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        return None
    try:
        import requests
        contexto = json.dumps(payload, ensure_ascii=False, default=str)[:_MAX_PAYLOAD_CHARS]
        resp = requests.post(
            _URL,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": os.getenv("AI_RESUMEN_MODEL", "deepseek-chat"),
                "max_tokens": _MAX_TOKENS,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": contexto},
                ],
            },
            timeout=_TIMEOUT_S,
        )
        if resp.status_code != 200:
            logger.warning("ai_resumen HTTP %s: %s", resp.status_code, resp.text[:200])
            return None
        texto = (resp.json()["choices"][0]["message"]["content"] or "").strip()
        return texto or None
    except Exception as e:
        logger.warning("ai_resumen falló: %s: %s", type(e).__name__, e)
        return None
