"""La forma de la respuesta final: `respuesta` y `falta`.

Dos caminos para lo mismo. Un agente SIN herramientas (la junta) recibe
`FORMATO` como esquema del proveedor cuando lo soporta. Un agente CON
herramientas no puede: OpenAI, con `response_format` en Chat Completions,
exige que toda herramienta sea `strict` (medido: «Only `strict` function
tools can be auto-parsed»), y DeepSeek no acepta esquema. Esos contestan en
prosa y marcan lo que no pudieron con un renglón final `Falta: …`; `leer`
entiende las dos formas."""
from __future__ import annotations

import json

NOMBRE = "respuesta_asistente"
FALTA_MARCA = "Falta:"
INSTRUCCION = (
    f"Si hubo algo que NO pudiste contestar, cerrá con un último renglón que empiece "
    f"con «{FALTA_MARCA}» y diga qué y por qué. Si contestaste todo, no lo pongas."
)

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


def _leer_prosa(crudo: str) -> dict:
    """Solo el ÚLTIMO renglón cuenta como `Falta:`. Uno en el medio es texto
    (y lo que venga después seguiría siendo respuesta)."""
    lineas = crudo.rstrip().splitlines()
    ultima = lineas[-1].strip() if lineas else ""
    if not ultima.startswith(FALTA_MARCA):
        return {"respuesta": crudo, "falta": None}
    respuesta = "\n".join(lineas[:-1]).strip()
    falta = ultima[len(FALTA_MARCA):].strip()
    return {"respuesta": respuesta or None, "falta": falta or None}


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
        return _leer_prosa(crudo)
    return {"respuesta": d.get("respuesta") or None, "falta": d.get("falta") or None}
