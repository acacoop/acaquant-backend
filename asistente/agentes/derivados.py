"""Agente DERIVADOS (familia mercado): un futuro o una opción. Modelado, todavía sin herramientas:
existe para que el despacho lo conozca y para que el asistente diga «eso todavía
no lo puedo consultar» en vez de inventar. Doc: docs/AvAgentAI.md.

Las primeras herramientas que van a entrar, con su pregunta:
  · «¿dónde cotiza el dólar futuro a diciembre?» → la curva de futuros con tasa implícita.
  · «¿qué opciones hay de GGAL?» → la cadena de opciones.
"""
from __future__ import annotations

from asistente.agente import COMUN, Agente

_INSTRUCCION = """
Hablás de DERIVADOS: futuros y opciones, dónde cotizan, qué tasa implícita
tienen, qué vencimientos hay. Sin importar quién los tenga.
"""


def _instruccion(_foco: dict) -> str:
    return COMUN + _INSTRUCCION


AGENTE = Agente(
    nombre="derivados",
    tarea="asistente_derivados",
    describe="futuros y opciones: dónde cotiza el dólar futuro, qué tasa implícita tiene, qué "
             "vencimientos hay, qué opciones hay de un papel.",
    instruccion=_instruccion,
    herramientas=(),
    senales=("futuro", "futuros", "opcion", "opciones", "call", "put", "strike", "implicita",
             "rofex", "a3", "dolar futuro"),
    familia="mercado",
    foco=("ticker",),
)
