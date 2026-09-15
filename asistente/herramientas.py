"""Cómo una función de Python se vuelve herramienta del modelo: docstring =
descripción, firma = esquema (`Literal` → enum). Las herramientas mismas
viven en `mundos/<mundo>.py`, al lado de su agente. Doc: docs/AvAgentAI.md."""
from __future__ import annotations

import json

from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from asistente.mundos import MUNDOS

# Techo del esquema que viaja al modelo por herramienta (chars de JSON).
MAX_FICHA_CHARS = 2_300

TODAS = tuple(f for a in MUNDOS.values() for f in a.herramientas)
POR_NOMBRE = {f.__name__: f for f in TODAS}


def como_tool(fn) -> StructuredTool:
    """La herramienta como `tool` de LangChain: docstring = descripción, firma = esquema."""
    return StructuredTool.from_function(fn)


def ficha(fn) -> dict:
    """El esquema que ve el modelo, en el dialecto del proveedor."""
    return convert_to_openai_tool(como_tool(fn))


def para_el_modelo(resultado):
    """El resultado sin las claves que empiezan con `_` (instrucciones de pantalla)."""
    if not isinstance(resultado, dict):
        return resultado
    return {k: v for k, v in resultado.items() if not str(k).startswith("_")}


def peso_ficha(fn) -> int:
    return len(json.dumps(ficha(fn), ensure_ascii=False))
