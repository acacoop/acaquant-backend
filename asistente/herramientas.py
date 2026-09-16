"""Cómo una función de Python se vuelve herramienta del modelo: docstring =
descripción, firma = esquema (`Literal` → enum). Las herramientas mismas
viven en `agentes/<agente>.py`, al lado de su agente. Doc: docs/AvAgentAI.md."""
from __future__ import annotations

import json

from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from asistente import pantalla
from asistente.agentes import AGENTES

# Techo del esquema que viaja al modelo por herramienta (chars de JSON). Es un
# tope de cordura, NO el presupuesto: lo que llega al proveedor es la SUMA de
# las herramientas de ese agente (cartera ya son ~6.200 chars entre sus tres).
# Subió de 2.300 a 3.000 cuando `instrumentos_de_la_curva` sumó la ventana de
# vencimiento: con el
# tope viejo, agregar un filtro obligaba a borrar la explicación de otro, y la
# explicación es lo único que evita que el modelo llame mal la herramienta —
# que es el error caro. El techo frena la verborragia, no la capacidad.
MAX_FICHA_CHARS = 3_000

TODAS = tuple(f for a in AGENTES.values() for f in a.herramientas)
POR_NOMBRE = {f.__name__: f for f in TODAS}


def como_tool(fn) -> StructuredTool:
    """La herramienta como `tool` de LangChain: docstring = descripción, firma = esquema."""
    return StructuredTool.from_function(fn)


def ficha(fn) -> dict:
    """El esquema que ve el modelo, en el dialecto del proveedor."""
    return convert_to_openai_tool(como_tool(fn))


def para_el_modelo(resultado):
    """El resultado como lo ve el modelo: sin las claves que empiezan con `_`
    (instrucciones de dibujo, no datos) y CON `se_muestra` si hubo tabla.

    Las dos mitades de la misma decisión: los datos de la tabla no viajan —
    ya están en el resultado— pero sí viaja que la tabla existe. Sin eso el
    modelo enumera lo mismo que la pantalla ya dibujó, porque no tiene cómo
    saber que se dibujó (`pantalla.aviso`)."""
    if not isinstance(resultado, dict):
        return resultado
    limpio = {k: v for k, v in resultado.items() if not str(k).startswith("_")}
    if (av := pantalla.aviso(resultado)) is not None:
        limpio["se_muestra"] = av
    return limpio


def peso_ficha(fn) -> int:
    return len(json.dumps(ficha(fn), ensure_ascii=False))
