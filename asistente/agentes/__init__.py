"""Los agentes del asistente: un archivo por agente, con sus herramientas y su
`AGENTE`. Sumar uno es crear el archivo, declarar su tarea en
`core/modelos.TAREAS` y agregarlo acá. Un test falla si el archivo existe y no
está registrado. Doc: docs/AvAgentAI.md."""
from __future__ import annotations

from asistente.agente import Agente, Familia
from asistente.agentes import (
    cartera,
    cliente,
    derivados,
    dolares,
    financiamiento,
    fondos,
    operaciones,
    renta_fija,
    renta_variable,
)

FAMILIAS: dict[str, Familia] = {
    "mercado": Familia(
        nombre="mercado",
        describe="qué hay y cuánto vale en el mercado, sin importar quién lo tenga",
        senales=("rinde", "rinden", "rendimiento", "rendimientos", "cotiza", "cotizan",
                 "cotizacion", "precio", "precios", "tasa", "tasas", "vence", "vencen",
                 "vencimiento", "vale", "valen", "ticker", "tamar", "mercado"),
    ),
}

AGENTES: dict[str, Agente] = {
    a.nombre: a for a in (
        cartera.AGENTE, cliente.AGENTE, operaciones.AGENTE,
        renta_fija.AGENTE, renta_variable.AGENTE, fondos.AGENTE, derivados.AGENTE,
        financiamiento.AGENTE, dolares.AGENTE,
    )
}
