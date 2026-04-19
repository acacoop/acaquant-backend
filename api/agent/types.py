"""Tipos neutros compartidos entre providers.

Usamos un formato canónico "estilo Claude" para mensajes y respuestas:
- role: "user" | "assistant"
- content: string O lista de bloques { type: "text"|"tool_use"|"tool_result", ... }

Cada provider (Gemini, Claude) traduce ese formato a/desde su propio shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCallRequest:
    """Una tool call solicitada por el modelo."""
    id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """Respuesta normalizada de cualquier provider."""
    text: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    stop_reason: str = "end_turn"  # end_turn | tool_use | max_tokens
    # El contenido "nativo" para apendear al history (ya en formato canónico).
    assistant_message: dict[str, Any] = field(default_factory=dict)


def user_text_message(text: str) -> dict[str, Any]:
    """Construye un mensaje user simple con texto."""
    return {"role": "user", "content": text}


def tool_result_message(tool_call_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Construye un mensaje user con el resultado de una tool call."""
    import json as _json
    content_str = _json.dumps(result, ensure_ascii=False)[:20000]
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": content_str,
            }
        ],
    }
