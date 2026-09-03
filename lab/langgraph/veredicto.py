"""`lab/langgraph/veredicto.py` — LA FORMA DE LA RESPUESTA, declarada UNA vez.

Una investigación no termina en prosa libre. Termina en **estos campos**, y el
modelo está obligado a llenarlos: el proveedor rechaza la respuesta que no
tenga la forma, así que no depende de que el prompt haya sido convincente.

⚠️⚠️ **UN CAMPO QUE ENUMERA COSAS ES UNA LISTA, NUNCA UN PÁRRAFO.**
La primera versión pedía texto libre y devolvía bloques de ochenta palabras:
una cronología, tres acciones y cuatro dudas, todo aplastado en un renglón que
nadie lee. El arreglo NO es un segundo modelo que reescriba —pagar dos veces
para tapar un error de diseño— sino cambiar lo que el modelo está OBLIGADO a
devolver. El tipo es la regla: `list[str]` y no hay forma de mandar un párrafo.

⚠️ **UN CAMPO SE DECLARA ACÁ Y EN NINGÚN OTRO LADO.** El texto que lee el
modelo (`description`) y el título que ve la persona (`title`) viven en la
MISMA línea, y el que dibuja RECORRE los campos en vez de nombrarlos. Por eso
este cambio de tipo llega solo a la terminal y a la pantalla: ninguna de las
dos sabe cuántos campos hay ni cómo se llaman.
"""
from __future__ import annotations

import textwrap
from typing import Literal

from pydantic import BaseModel, Field

DE_QUIEN = Literal["nuestro", "dato", "proveedor", "no_se"]


class Veredicto(BaseModel):
    """El resultado de una investigación."""

    titulo: str = Field(
        title="EN UNA LÍNEA",
        description="La conclusión en UNA frase de menos de 120 caracteres, "
                    "que se entienda sin leer el resto. Ej.: «El alta funcionó; "
                    "lo borró el cleanup por vencimiento».")

    que_paso: list[str] = Field(
        title="QUÉ PASÓ",
        description="La cronología, UN HECHO POR ELEMENTO, en orden y cada uno "
                    "arrancando con su fecha y hora. Sin interpretación. "
                    "Máximo 6 elementos, de una línea cada uno.")

    por_que: list[str] = Field(
        title="POR QUÉ",
        description="La causa, UNA IDEA POR ELEMENTO. Si son dos procesos que "
                    "se contradicen, un elemento por proceso diciendo qué hace "
                    "cada uno. Máximo 4 elementos cortos.")

    de_quien_es: DE_QUIEN = Field(
        title="DE QUIÉN ES",
        description="'nuestro' (nuestro código o config), 'dato' (algo mal "
                    "cargado), 'proveedor' (falló una fuente externa), "
                    "'no_se' si la evidencia no alcanza.")

    que_haria: list[str] = Field(
        title="QUÉ HARÍA",
        description="UNA ACCIÓN POR ELEMENTO, concreta y en imperativo: qué "
                    "campo, qué job, qué archivo. NO «revisar» ni «analizar». "
                    "Si ya está resuelto, un elemento que lo diga. Si quedó "
                    "algo pendiente que no te preguntaron, va igual. Máximo 4.")

    lo_que_no_se: list[str] = Field(
        title="LO QUE NO SÉ",
        description="UNA POR ELEMENTO: qué NO pudiste verificar y qué haría "
                    "falta para saberlo. NUNCA vacío: siempre hay algo que no "
                    "se miró. Máximo 4 elementos de una línea.")

    de_donde: list[str] = Field(
        title="DE DÓNDE LO SAQUÉ",
        description="Las fuentes concretas: tablas consultadas y archivos con "
                    "su línea. Una afirmación sin fuente no vale.")


# Los campos que son listas, derivado del modelo. Lo usa la pantalla para saber
# qué dibujar como viñetas sin tener escrita una lista paralela de nombres.
LISTAS = tuple(n for n, c in Veredicto.model_fields.items()
               if getattr(c.annotation, "__origin__", None) is list)


def render(v: dict | Veredicto, ancho: int = 62) -> str:
    """Lo dibuja para la terminal. **Recorre los campos, no los nombra.**

    Por eso agregar un campo arriba lo hace aparecer acá solo, y por eso el día
    que esto se muestre en una pantalla web se cambia únicamente esta función.

    ⚠️ Recibe DATOS PLANOS, no el objeto. El veredicto viaja por el estado del
    grafo y ese estado se serializa: LangGraph avisa que deserializar una clase
    propia **va a estar bloqueado en una versión futura**. Un dict no tiene ese
    problema, y encima es lo que consume la pantalla.
    """
    datos = v if isinstance(v, dict) else v.model_dump()
    out = []
    for nombre, campo in Veredicto.model_fields.items():
        titulo = campo.title or nombre.upper()
        valor = datos.get(nombre) or ""
        if isinstance(valor, list):
            cuerpo = "\n".join(
                "\n".join(textwrap.wrap(str(x), ancho - 4, initial_indent="  · ",
                                        subsequent_indent="    "))
                for x in valor)
        else:
            cuerpo = "\n".join(textwrap.wrap(str(valor), ancho - 2,
                                             initial_indent="  ",
                                             subsequent_indent="  "))
        out.append(f"─── {titulo} " + "─" * max(3, ancho - len(titulo) - 5))
        out.append(cuerpo or "  (vacío)")
        out.append("")
    return "\n".join(out)
