"""Agente RENTA VARIABLE (familia mercado): una acción o un CEDEAR. Modelado, todavía sin herramientas:
existe para que el despacho lo conozca y para que el asistente diga «eso todavía
no lo puedo consultar» en vez de inventar. Doc: docs/AvAgentAI.md.

Las primeras herramientas que van a entrar, con su pregunta:
  · «¿cómo cotiza YPF?» / «¿cuánto varió hoy?» → la cotización con variación diaria.
  · «¿qué hay en el panel líder?» → el panel, ordenado por variación o volumen.
"""
from __future__ import annotations

from asistente.agente import COMUN, Agente

_INSTRUCCION = """
Hablás de RENTA VARIABLE: acciones y CEDEARs, cómo cotizan, cuánto varían,
qué panel, qué subyacente. Sin importar quién las tenga.
"""


def _instruccion(_foco: dict) -> str:
    return COMUN + _INSTRUCCION


AGENTE = Agente(
    nombre="renta_variable",
    tarea="asistente_renta_variable",
    describe="acciones y CEDEARs: cómo cotiza una acción, cuánto varió, qué hay en un panel, "
             "qué subyacente tiene un CEDEAR.",
    instruccion=_instruccion,
    herramientas=(),
    senales=("accion", "acciones", "cedear", "cedears", "merval", "panel", "subyacente", "papel",
             "papeles", "equity"),
    familia="mercado",
    foco=("ticker",),
)
