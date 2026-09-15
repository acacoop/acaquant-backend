"""La forma de la respuesta final cuando el proveedor soporta esquema: dos
campos, `respuesta` y `falta`. Si el proveedor no lo soporta, la prosa es la
respuesta y `falta` queda en null."""
from __future__ import annotations

import json

NOMBRE = "respuesta_asistente"

FORMATO = {
    "type": "json_schema",
    "json_schema": {
        "name": NOMBRE,
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "respuesta": {
                    "type": "string",
                    "description": ("Tu respuesta en castellano, corta y "
                                    "concreta. Sin asteriscos ni markdown; si "
                                    "hay que enumerar, una lista simple."),
                },
                "falta": {
                    "type": ["string", "null"],
                    "description": ("Qué NO pudiste contestar y por qué, o "
                                    "null. Si te pidieron algo que ninguna "
                                    "herramienta devuelve, va acá — no lo "
                                    "calcules."),
                },
            },
            "required": ["respuesta", "falta"],
            "additionalProperties": False,
        },
    },
}


def leer(texto: str | None) -> dict:
    """`{respuesta, falta}` desde lo que contestó el modelo. Nunca levanta."""
    crudo = (texto or "").strip()
    if not crudo:
        return {"respuesta": None, "falta": None}
    try:
        d = json.loads(crudo)
        if not isinstance(d, dict) or "respuesta" not in d:
            raise ValueError("no tiene la forma esperada")
    except (ValueError, TypeError):
        return {"respuesta": crudo, "falta": None}
    return {"respuesta": d.get("respuesta") or None, "falta": d.get("falta") or None}
