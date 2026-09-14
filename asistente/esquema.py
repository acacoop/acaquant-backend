"""asistente/esquema.py — LA FORMA QUE TIENE QUE TENER LA RESPUESTA.

Cuando el proveedor lo soporta, la respuesta final deja de ser texto libre y
pasa a ser un JSON de dos campos que el proveedor **fuerza** (no es un pedido
del prompt):

    respuesta  la prosa, en castellano
    falta      qué no pudo contestar, o null

⚠️ **HUBO UN TERCER CAMPO, `mostrar`, Y SE BORRÓ (decisión del user).** El
modelo nombraba qué partes del resultado dibujar como tabla y la pantalla las
dibujaba. El campo era una lista sin tope y sin criterio, así que a «¿cuánto
cobro?» contestaba con TRES tablas del mismo total. El arreglo no era ponerle
tope: una tabla no era la forma de contestar. La lista en prosa sí, y por eso
`respuesta` ya no la prohíbe.

`leer()` NUNCA levanta. También corre cuando el proveedor no soporta esquema y
contestó prosa: ahí la prosa ES la respuesta.
"""
from __future__ import annotations

import json

NOMBRE = "respuesta_asistente"

# Fijo: no depende del turno, así que se arma una vez y viaja igual en todas las
# vueltas. `strict` + `additionalProperties: False` es lo que lo hace una pared
# y no una sugerencia — el proveedor RECHAZA una respuesta con otra forma.
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
    """Lo que contestó el modelo, como `{respuesta, falta}`."""
    crudo = (texto or "").strip()
    if not crudo:
        return {"respuesta": None, "falta": None}
    try:
        d = json.loads(crudo)
        if not isinstance(d, dict) or "respuesta" not in d:
            raise ValueError("no tiene la forma esperada")
    except (ValueError, TypeError):
        # No es JSON: es prosa. El proveedor no soporta esquema, o lo ignoró.
        return {"respuesta": crudo, "falta": None}
    return {"respuesta": d.get("respuesta") or None, "falta": d.get("falta") or None}
