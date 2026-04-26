"""Flows estructurados (structured intake) del asistente.

Cada flow vive en su propio módulo: schema Pydantic del input, tool de
output forzada via tool_choice, system prompt addendum, y construcción
determinística del user message.

La idea es separar el procesamiento determinístico de cada flow del
runner genérico — el runner sigue ejecutando el loop ReAct con tools de
data, lo que cambia es el shape del input/output por flow.

Patrón actual:
- `cartera`: recomendar cartera por perfil/exposición/plazo/benchmark.

Próximos (TODO): `analisis_bono`, `comparar_curvas`.
"""
from api.agent.structured.cartera import (
    RESPONDER_CARTERA_TOOL,
    CarteraRequest,
    validar_pesos_suman_100,
)
from api.agent.structured.cartera import (
    SYSTEM_PROMPT_ADDENDUM as CARTERA_ADDENDUM,
)
from api.agent.structured.cartera import (
    build_user_message as build_user_message_cartera,
)

__all__ = [
    "CARTERA_ADDENDUM",
    "RESPONDER_CARTERA_TOOL",
    "CarteraRequest",
    "build_user_message_cartera",
    "validar_pesos_suman_100",
]
