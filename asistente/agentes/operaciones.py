"""Agente OPERACIONES: qué HACE la mesa. El libro de operaciones de la empresa:
qué se compró y vendió, en qué títulos, por cuánto, con qué arancel. La cuenta
es un filtro, no el sujeto: el sujeto es la mesa. Doc: docs/AvAgentAI.md.

Todavía no tiene herramientas: existe para que el despacho lo conozca y para
que el asistente diga «eso todavía no lo puedo consultar» en vez de inventar.
Las primeras que van a entrar, con su pregunta:
  · «¿qué se operó hoy / en el mes, por título o por cliente?» → un consolidado
    sobre `api/services/operaciones_sql.ops_consolidado` con el alcance de
    cuentas del asistente.
  · «¿qué boletos hubo de tal título o tal cuenta?» → el listado de boletos.
"""
from __future__ import annotations

from asistente.agente import COMUN, Agente

_INSTRUCCION = """
Hablás de lo que la mesa OPERÓ: boletos, volumen, aranceles, por título, por
cliente, por día. Movimiento, no posición: qué se compró y vendió, no qué se
tiene. La cuenta es un filtro más, como el título o la fecha.
"""


def _instruccion(_foco: dict) -> str:
    return COMUN + _INSTRUCCION


AGENTE = Agente(
    nombre="operaciones",
    tarea="asistente_operaciones",
    describe="qué HIZO la mesa: qué se compró y vendió, en qué títulos, por cuánto, con qué "
             "arancel, qué boletos hubo. Movimiento, no posición. La cuenta es un filtro, "
             "no hace falta.",
    instruccion=_instruccion,
    herramientas=(),
    senales=("compro", "compraron", "vendio", "vendieron", "opero", "operaron", "operacion",
             "operaciones", "boleto", "boletos", "arancel", "aranceles", "volumen", "concerto",
             "cierre", "cierres"),
    foco=("cuenta", "ticker"),
)
