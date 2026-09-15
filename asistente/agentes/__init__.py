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


def por_familia() -> dict[str, list[Agente]]:
    """Los agentes agrupados por familia, en el orden del registro. Los que no
    tienen familia van bajo la clave vacía, primero."""
    out: dict[str, list[Agente]] = {"": []}
    for a in AGENTES.values():
        out.setdefault(a.familia, []).append(a)
    return {k: v for k, v in out.items() if v}


def presentacion() -> str:
    """Qué sabe hacer el asistente, escrito desde el registro: nunca queda
    viejo. Es la respuesta a «¿qué sabés hacer?»."""
    lineas = ["Puedo mirar estas cosas, siempre con datos de la plataforma:"]
    for familia, agentes in por_familia().items():
        if familia:
            lineas.append(f"{familia}:")
        for a in agentes:
            herramientas = ", ".join(f.__name__ for f in a.herramientas) or "todavía ninguna"
            lineas.append(f"{'  ' if familia else ''}- {a.nombre}: {a.describe} "
                          f"Herramientas: {herramientas}.")
    lineas.append("No escribo nada ni invento datos: si una herramienta no lo trae, no lo digo.")
    return "\n".join(lineas)
