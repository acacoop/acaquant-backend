"""Runner del tool-use loop (provider-agnostic).

Flujo:
1. Recibimos el mensaje del usuario + history.
2. `router.decide_model()` elige haiku o sonnet para este turno.
3. `get_provider()` nos devuelve el cliente (Claude o Gemini según LLM_PROVIDER).
4. Armamos el system prompt dinámico (framework + context + data inventory on demand).
5. Loop: modelo decide → si text final, respondemos; si tool_use, ejecutamos
   la tool y volvemos al modelo con el resultado.
6. Corte a MAX_STEPS para controlar costo.

El history que entra/sale está en formato CANÓNICO (estilo Claude):
  [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": [{"type": "text", ...} | {"type": "tool_use", ...}]},
    {"role": "user", "content": [{"type": "tool_result", ...}]},
    ...
  ]

Si el history llega en formato Gemini viejo (items con `parts`), lo descartamos
silenciosamente — el usuario arranca la conversación de cero esa vez.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from api.agent.context import build_market_context
from api.agent.data_inventory import build_data_inventory
from api.agent.prompt import build_system_prompt
from api.agent.provider import (
    LLMError,
    LLMProvider,
    get_provider,
)
from api.agent.router import decide_model
from api.agent.tools import dispatch
from api.agent.types import LLMResponse, tool_result_message, user_text_message

logger = logging.getLogger(__name__)

# Tope de turnos encadenados. Controla costo ante loops de tool-use.
MAX_STEPS = 6


def _is_legacy_gemini_history(history: list[dict[str, Any]] | None) -> bool:
    if not history:
        return False
    for m in history:
        if isinstance(m, dict) and "parts" in m:
            return True
    return False


def _sanitize_history(history: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Limpia history en formato canónico o descarta si es legacy."""
    if _is_legacy_gemini_history(history):
        logger.info("history legacy (Gemini) detectado — descartando")
        return []
    return [m for m in (history or []) if isinstance(m, dict) and "role" in m]


def run_conversation(
    user_message: str,
    history: list[dict[str, Any]] | None = None,
    *,
    provider: LLMProvider | None = None,
    force_model: str | None = None,
    tools_list: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Corre una vuelta de conversación.

    Retorna:
        {
            "reply": str,
            "tool_calls": [{"name", "args", "ok"}],
            "history": [...]           # formato canónico para persistir
            "usage": {"promptTokenCount", "candidatesTokenCount", "totalTokenCount", "model", "model_alias"}
            "steps": int,
            "elapsed_s": float,
            "truncated": bool,
            "model_used": str          # alias (haiku | sonnet | gemini-flash)
        }
    """
    # Tools list del sistema (si no se pasa, cargar las definidas en tools.py)
    if tools_list is None:
        from api.agent.tools import TOOLS, _is_blocked
        tools_list = [t for t in TOOLS if not _is_blocked(t["endpoint"])]

    # Decidir modelo y provider
    if provider is None:
        model_alias = decide_model(user_message, force=force_model)
        provider = get_provider(model_alias)
    model_used = getattr(provider, "alias", getattr(provider, "model", "unknown"))

    # Mensajes canónicos
    messages: list[dict[str, Any]] = _sanitize_history(history)
    messages.append(user_text_message(user_message))

    # System prompt dinámico (cacheable por Claude)
    try:
        market_ctx = build_market_context()
    except Exception:
        logger.exception("fallo market_context; sigo")
        market_ctx = ""
    try:
        data_inv = build_data_inventory()
    except Exception:
        logger.exception("fallo data_inventory; sigo")
        data_inv = ""
    system_prompt = build_system_prompt(market_ctx, data_inv)

    tool_calls_log: list[dict[str, Any]] = []
    last_usage: dict[str, Any] = {}
    t_start = time.time()

    for step in range(MAX_STEPS):
        try:
            resp: LLMResponse = provider.generate(
                messages=messages,
                system_prompt=system_prompt,
                tools=tools_list,
            )
        except LLMError:
            raise
        except Exception as e:
            logger.exception("error no controlado del provider")
            raise LLMError(f"error del provider: {e}") from e

        last_usage = resp.usage

        # Apendear mensaje del asistente al history SIEMPRE
        messages.append(resp.assistant_message)

        # Si no pidió tools, cerramos
        if not resp.tool_calls:
            return {
                "reply": resp.text or "(respuesta vacía)",
                "tool_calls": tool_calls_log,
                "history": messages,
                "usage": last_usage,
                "steps": step + 1,
                "elapsed_s": round(time.time() - t_start, 2),
                "truncated": False,
                "model_used": model_used,
            }

        # Ejecutar cada tool call y apendear los resultados
        for tc in resp.tool_calls:
            logger.info("tool_call name=%s args=%s", tc.name, tc.args)
            result = dispatch(tc.name, tc.args)
            tool_calls_log.append({
                "name": tc.name,
                "args": tc.args,
                "ok": bool(result.get("ok", False)),
            })
            messages.append(tool_result_message(tc.id, result))

    # Llegamos al tope
    logger.warning("MAX_STEPS=%d alcanzado", MAX_STEPS)
    return {
        "reply": (
            "Corté el procesamiento para controlar costos: el modelo pidió más de "
            f"{MAX_STEPS} consultas encadenadas. Reformulá la pregunta siendo más "
            "específico."
        ),
        "tool_calls": tool_calls_log,
        "history": messages,
        "usage": last_usage,
        "steps": MAX_STEPS,
        "elapsed_s": round(time.time() - t_start, 2),
        "truncated": True,
        "model_used": model_used,
    }
