"""Tipos neutros compartidos entre providers.

Usamos un formato canónico "estilo Claude" para mensajes y respuestas:
- role: "user" | "assistant"
- content: string O lista de bloques { type: "text"|"tool_use"|"tool_result", ... }

Cada provider (Gemini, Claude) traduce ese formato a/desde su propio shape.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
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


def _json_default(o: Any) -> str:
    """Handler de `json.dumps` para tipos no-JSON-serializables nativos.

    Los tools del agente leen directo de Mongo y pueden devolver `datetime`
    (y, por extensión, `date`) crudos en el payload. Sin este handler el
    dumps tira TypeError y el turno del asistente muere en el runner.

    Cualquier otro tipo raro se cae a `str(o)` — mejor eso que reventar.
    """
    if isinstance(o, datetime | date):
        return o.isoformat()
    return str(o)


def user_text_message(text: str) -> dict[str, Any]:
    """Construye un mensaje user simple con texto."""
    return {"role": "user", "content": text}


def tool_result_message(tool_call_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Construye un mensaje user con el resultado de una tool call."""
    return tool_results_message([(tool_call_id, result)])


def tool_results_message(
    items: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    """Construye UN solo mensaje user con N bloques tool_result.

    Anthropic exige que cuando el assistant_message previo tiene N tool_use,
    el siguiente user_message contenga los N tool_result en bloques dentro
    del MISMO mensaje. Mandarlos como N mensajes separados rompe con
    "tool_use ids were found without tool_result blocks immediately after".
    """
    blocks = [
        {
            "type": "tool_result",
            "tool_use_id": tid,
            "content": json.dumps(result, ensure_ascii=False, default=_json_default)[:20000],
        }
        for tid, result in items
    ]
    return {"role": "user", "content": blocks}
