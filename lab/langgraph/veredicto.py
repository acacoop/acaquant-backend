"""`lab/langgraph/veredicto.py` — LA FORMA DE LA RESPUESTA, declarada UNA vez.

Una investigación no termina en prosa libre. Termina en **estos campos**, y el
modelo está obligado a llenarlos: el proveedor rechaza la respuesta que no
tenga la forma, así que no depende de que el prompt haya sido convincente.

⚠️ **UN CAMPO SE DECLARA ACÁ Y EN NINGÚN OTRO LADO.** El texto que lee el
modelo (`description`) y el título que ve la persona (`title`) viven en la
MISMA línea. Sumar un campo es una línea; el que dibuja no se entera, porque
recorre los campos en vez de nombrarlos de a uno.

Es la razón por la que esto va a poder mostrarse en la pantalla del AV AGENT
sin tocar el agente: cambiar CÓMO se ve no es cambiar QUÉ se responde.
"""
from __future__ import annotations

import textwrap
from typing import Literal

from pydantic import BaseModel, Field

DE_QUIEN = Literal["nuestro", "dato", "proveedor", "no_se"]


class Veredicto(BaseModel):
    """El resultado de una investigación."""

    que_paso: str = Field(
        title="QUÉ PASÓ",
        description="Los hechos, en orden, con fecha y hora. Sin interpretación.")

    por_que: str = Field(
        title="POR QUÉ",
        description="La causa. Si son dos procesos que se contradicen, decir "
                    "cuáles y qué hace cada uno.")

    de_quien_es: DE_QUIEN = Field(
        title="DE QUIÉN ES",
        description="'nuestro' (nuestro código o config), 'dato' (algo mal "
                    "cargado), 'proveedor' (falló una fuente externa), "
                    "'no_se' si la evidencia no alcanza.")

    que_haria: str = Field(
        title="QUÉ HARÍA",
        description="La acción concreta: qué campo, qué job, qué archivo. NO "
                    "'revisar' ni 'analizar'. Si ya está resuelto, decirlo — y "
                    "si queda algo pendiente aunque no lo hayan preguntado, "
                    "decirlo también.")

    lo_que_no_se: str = Field(
        title="LO QUE NO SÉ",
        description="Qué NO pudiste verificar y qué haría falta para saberlo. "
                    "Nunca vacío: siempre hay algo que no se miró.")

    de_donde: list[str] = Field(
        title="DE DÓNDE LO SAQUÉ",
        description="Las fuentes concretas: tablas consultadas y archivos con "
                    "su línea. Una afirmación sin fuente no vale.")


def render(v: Veredicto, ancho: int = 62) -> str:
    """Lo dibuja para la terminal. **Recorre los campos, no los nombra.**

    Por eso agregar un campo arriba lo hace aparecer acá solo, y por eso el día
    que esto se muestre en una pantalla web se cambia únicamente esta función.
    """
    out = []
    for nombre, campo in Veredicto.model_fields.items():
        titulo = campo.title or nombre.upper()
        valor = getattr(v, nombre)
        # ⚠️ Se corta a lo ancho de la terminal. Un párrafo de 400 caracteres en
        # una sola línea es ilegible aunque el contenido sea correcto — y la
        # forma existe justamente para que se entienda.
        if isinstance(valor, list):
            cuerpo = "\n".join(f"  · {x}" for x in valor)
        else:
            cuerpo = "\n".join(
                "\n".join(textwrap.wrap(ln, ancho - 2,
                                        initial_indent="  ", subsequent_indent="  "))
                if ln.strip() else ""
                for ln in str(valor).splitlines())
        out.append(f"─── {titulo} " + "─" * max(3, ancho - len(titulo) - 5))
        out.append(cuerpo or "  (vacío)")
        out.append("")
    return "\n".join(out)
