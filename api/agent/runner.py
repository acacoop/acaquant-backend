"""Runner del tool-use loop.

Flujo:
1. Recibimos mensajes del usuario.
2. Le pasamos a Gemini con el listado de tools.
3. Si Gemini pide una tool, la ejecutamos y le devolvemos el resultado.
4. Loop hasta que Gemini responda texto final (sin functionCall) o se alcance MAX_STEPS.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from api.agent.prompt import SYSTEM_PROMPT
from api.agent.provider import GeminiProvider, LLMError
from api.agent.tools import dispatch, gemini_tool_declarations

logger = logging.getLogger(__name__)

# Máximo de tool-calls encadenadas. Evita loops y controla costo.
MAX_STEPS = 8


def _user_message(text: str) -> dict[str, Any]:
    return {"role": "user", "parts": [{"text": text}]}


def _function_response_message(name: str, result: dict[str, Any]) -> dict[str, Any]:
    # En Gemini las function responses van con role="user".
    return {
        "role": "user",
        "parts": [{"functionResponse": {"name": name, "response": result}}],
    }


def run_conversation(
    provider: GeminiProvider,
    user_message: str,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Corre una vuelta de conversación.

    history: mensajes previos (formato Gemini). Si None, se arranca desde cero.
    Devuelve:
        {
            "reply": str,               # texto final del modelo
            "tool_calls": [...],        # para auditar qué consultó
            "history": [...],           # para enviarlo de vuelta en el siguiente turno
            "usage": {...},             # tokens del último turno
        }
    """
    contents: list[dict[str, Any]] = list(history or [])
    contents.append(_user_message(user_message))

    tools = gemini_tool_declarations()
    tool_calls: list[dict[str, Any]] = []

    t_start = time.time()
    last_usage: dict[str, Any] = {}

    for step in range(MAX_STEPS):
        try:
            candidate = provider.generate(contents=contents, system_prompt=SYSTEM_PROMPT, tools=tools)
        except LLMError as e:
            logger.exception("LLM error en step %d", step)
            raise

        last_usage = candidate.get("usageMetadata", {})
        parts = candidate.get("content", {}).get("parts", [])
        model_message = {"role": "model", "parts": parts}

        # Separamos functionCalls de texto.
        function_calls = [p["functionCall"] for p in parts if "functionCall" in p]
        texts = [p["text"] for p in parts if "text" in p and p["text"]]

        if not function_calls:
            # Respuesta final. Guardamos el mensaje del modelo y salimos.
            contents.append(model_message)
            reply = "\n".join(texts).strip() or "(respuesta vacía)"
            return {
                "reply": reply,
                "tool_calls": tool_calls,
                "history": contents,
                "usage": last_usage,
                "steps": step + 1,
                "elapsed_s": round(time.time() - t_start, 2),
            }

        # Agregamos el mensaje del modelo con los functionCalls al historial.
        contents.append(model_message)

        # Ejecutamos cada functionCall pedida y le devolvemos la respuesta.
        for fc in function_calls:
            name = fc.get("name", "")
            args = fc.get("args", {}) or {}
            logger.info("tool_call name=%s args=%s", name, args)
            result = dispatch(name, args)
            tool_calls.append({"name": name, "args": args, "ok": result.get("ok", False)})
            contents.append(_function_response_message(name, result))

    # Si llegamos acá, el modelo entró en loop de tools.
    logger.warning("Conversación cortada por MAX_STEPS=%d", MAX_STEPS)
    return {
        "reply": (
            "Corté el procesamiento para controlar costos: el modelo pidió "
            f"más de {MAX_STEPS} consultas encadenadas. Reformulá la pregunta "
            "siendo más específico."
        ),
        "tool_calls": tool_calls,
        "history": contents,
        "usage": last_usage,
        "steps": MAX_STEPS,
        "elapsed_s": round(time.time() - t_start, 2),
        "truncated": True,
    }
