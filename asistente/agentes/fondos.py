"""Agente FONDOS (familia mercado): un fondo común de inversión. Modelado, todavía sin herramientas:
existe para que el ruteo lo conozca y para que el asistente diga «eso todavía
no lo puedo consultar» en vez de inventar. Doc: docs/AvAgentAI.md.

Las primeras herramientas que van a entrar, con su pregunta:
  · «¿qué es tal fondo / cuánto rinde?» → la ficha del FCI.
  · «¿qué fondo money market rinde más?» → el ranking por clase.
"""
from __future__ import annotations

from asistente.agente import COMUN, Agente

_INSTRUCCION = """
Hablás de FONDOS comunes de inversión: qué es un fondo, cuánto rinde, qué
tiene adentro, cuánto tarda el rescate. Sin importar quién lo tenga.
"""


def _instruccion(_foco: dict) -> str:
    return COMUN + _INSTRUCCION


AGENTE = Agente(
    nombre="fondos",
    tarea="asistente_fondos",
    describe="fondos comunes de inversión (FCI): qué es un fondo, cuánto rinde, qué tiene "
             "adentro, cuánto tarda el rescate, qué fondo de una clase rinde más.",
    instruccion=_instruccion,
    herramientas=(),
    senales=("fondo", "fondos", "fci", "money market", "rescate", "suscripcion", "cuotaparte",
             "cuotapartes"),
    familia="mercado",
    foco=("ticker",),
)
