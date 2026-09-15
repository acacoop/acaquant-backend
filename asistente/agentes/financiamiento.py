"""Agente FINANCIAMIENTO (familia mercado): la tasa. Modelado, todavía sin herramientas:
existe para que el despacho lo conozca y para que el asistente diga «eso todavía
no lo puedo consultar» en vez de inventar. Doc: docs/AvAgentAI.md.

Las primeras herramientas que van a entrar, con su pregunta:
  · «¿cuánto rinde la caución a 7 días?» → la caución por plazo.
  · «¿a cuánto está la TAMAR / BADLAR?» → las tasas de referencia.
"""
from __future__ import annotations

from asistente.agente import COMUN, Agente

_INSTRUCCION = """
Hablás de FINANCIAMIENTO: caución por plazo y tasas de referencia. Cuánto
cuesta financiarse y cuánto paga colocar.
"""


def _instruccion(_foco: dict) -> str:
    return COMUN + _INSTRUCCION


AGENTE = Agente(
    nombre="financiamiento",
    tarea="asistente_financiamiento",
    describe="caución y tasas: cuánto rinde la caución a N días, a cuánto están las tasas de "
             "referencia (TAMAR, BADLAR), cuánto cuesta financiarse.",
    instruccion=_instruccion,
    herramientas=(),
    senales=("caucion", "cauciones", "badlar", "financiar", "financiarse", "financiarme",
             "financiamiento", "financiacion", "colocar", "plazo fijo", "tasa de referencia"),
    familia="mercado",
    foco=(),
)
