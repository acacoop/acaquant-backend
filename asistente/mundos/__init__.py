"""Los mundos del asistente: un archivo por mundo, con sus herramientas y su
agente (`AGENTE`). Sumar uno es crear el archivo, declarar su tarea en
`core/modelos.TAREAS` y agregarlo acá. Un test falla si el archivo existe y no
está registrado. Doc: docs/AvAgentAI.md."""
from __future__ import annotations

from asistente.agente import Agente
from asistente.mundos import cartera, cliente, mercado, operaciones

MUNDOS: dict[str, Agente] = {
    a.nombre: a for a in (cartera.AGENTE, cliente.AGENTE, operaciones.AGENTE, mercado.AGENTE)
}
