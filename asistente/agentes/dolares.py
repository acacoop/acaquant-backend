"""Agente DÓLARES (familia mercado): el tipo de cambio. Modelado, todavía sin herramientas:
existe para que el despacho lo conozca y para que el asistente diga «eso todavía
no lo puedo consultar» en vez de inventar. Doc: docs/AvAgentAI.md.

Las primeras herramientas que van a entrar, con su pregunta:
  · «¿a cuánto está el MEP / el CCL?» → los dólares de hoy con sus brechas.
"""
from __future__ import annotations

from asistente.agente import COMUN, Agente

_INSTRUCCION = """
Hablás de DÓLARES: MEP, CCL, oficial, brechas entre ellos. El tipo de cambio,
no los bonos con los que se arma.
"""


def _instruccion(_foco: dict) -> str:
    return COMUN + _INSTRUCCION


AGENTE = Agente(
    nombre="dolares",
    tarea="asistente_dolares",
    describe="el tipo de cambio: MEP, CCL, oficial, brechas. A cuánto está el dólar hoy.",
    instruccion=_instruccion,
    herramientas=(),
    senales=("mep", "ccl", "oficial", "brecha", "brechas", "tipo de cambio", "blue", "el dolar",
             "del dolar", "dolar hoy", "dolar futuro"),
    familia="mercado",
    foco=(),
)
