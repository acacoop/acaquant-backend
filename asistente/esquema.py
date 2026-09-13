"""asistente/esquema.py — LA FORMA QUE TIENE QUE TENER LA RESPUESTA.

── SU ROL EN EL CICLO: es lo último, y no cambia nada de lo anterior ──

Cuando el proveedor lo soporta, la respuesta FINAL del modelo deja de ser texto
libre y pasa a ser un JSON con tres campos. No es un pedido del prompt: el
proveedor lo **fuerza**.

El ciclo no se entera: las vueltas que piden herramientas siguen igual (medido
en `scripts/diag_structured_output.py` — el proveedor entiende que un pedido de
herramienta no es «la respuesta final» y no le exige la forma). Lo único que
cambia es la forma del último mensaje.

⚠️⚠️ **LA DECISIÓN QUE HACE QUE ESTO SIRVA: EL MODELO NO ESCRIBE DATOS.**

Había dos formas de armarlo, y la que parece obvia es una trampa:

  ❌ que el modelo arme la tabla   → re-tipea 20 números, paga tokens de salida
                                     por copiar lo que ya teníamos, y crea un
                                     lugar NUEVO donde inventar plata — justo
                                     después de que pusimos un control para
                                     detectarla.

  ✅ que el modelo diga QUÉ MOSTRAR → nombra campos del resultado, y los datos
                                     los dibuja la pantalla leyendo ese mismo
                                     resultado. No escribe un solo número de la
                                     tabla, así que no puede errarle.

Por eso el esquema tiene tres campos y ninguno lleva datos:

    respuesta  la prosa, en castellano
    mostrar    qué partes del resultado dibujar, por nombre
    falta      qué no pudo contestar (o null)

Y por eso es chico: un esquema que describe el CONTENIDO sólo sirve para las
preguntas que tienen ese contenido. Éste describe la CONVERSACIÓN, así que
entra en cualquier pregunta — incluso en una sin tablas, con `mostrar` vacío.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

NOMBRE = "respuesta_asistente"

# Cuántos campos como mucho entran en el `enum`. Cada uno son tokens que viajan
# en la llamada final. Con cinco herramientas de ocho campos son cuarenta, que
# es nada; el techo está para que una herramienta que devuelva un diccionario
# gigante no infle la llamada sin que nadie lo note.
MAX_MOSTRABLES = 60

# Los pocos nombres que son metadatos aunque vengan como lista. El resto se
# resuelve por FORMA, no por nombre (ver abajo): una lista de nombres de campos
# acá adentro acoplaría este archivo a las herramientas, que es justo lo que no
# tiene que pasar.
NO_MOSTRABLES = frozenset({"cuentas_habilitadas"})


def campos_mostrables(resultados: dict[str, object]) -> list[str]:
    """Los nombres que el modelo PUEDE pedir que se muestren.

    ── SU ROL: convierte una promesa en una pared ──

    `resultados` es `{herramienta: lo que devolvió}` de ESTE turno. De ahí salen
    los nombres, así que:

      · una herramienta nueva aporta sus campos SOLA — no hay una segunda lista
        que mantener al día, igual que la ficha del modelo;
      · y el modelo **no puede nombrar un campo que no existe**, porque el enum
        no lo contiene. No es que se porta bien: es que el proveedor no lo deja.

    ⚠️ El nombre va COMPLETO (`herramienta.campo`). Un turno puede llamar a dos
    herramientas, y las dos pueden devolver un campo `total` — uno es lo que
    cobrás y el otro lo que valen tus posiciones. Con el nombre corto la
    pantalla dibujaría uno de los dos sin forma de saber cuál quiso el modelo.

    ⚠️ **SÓLO LAS LISTAS, Y LA REGLA ES DE FORMA, NO DE NOMBRE.** Una tabla es
    una lista de filas; todo lo demás se lee mejor en la prosa. Así quedan
    afuera solas `ventana` (el rango de fechas), `cuenta` (de quién es) y
    `total` (`{"USD": 611.83}`) — que son CONTEXTO, no datos para dibujar.

    Filtrarlas por nombre habría acoplado este archivo a `cobros_futuros`, y con
    la próxima herramienta habría que venir a agregar los suyos. Por forma, una
    herramienta nueva se filtra sola y nadie toca esto.
    """
    fuera: list[str] = []
    for herramienta, r in (resultados or {}).items():
        if not isinstance(r, dict):
            continue
        for campo, valor in r.items():
            if campo in NO_MOSTRABLES or campo.startswith("_"):
                continue
            if not isinstance(valor, list) or not valor:
                continue
            fuera.append(f"{herramienta}.{campo}")
    if len(fuera) > MAX_MOSTRABLES:
        logger.warning("esquema: %s campos mostrables, recorto a %s",
                       len(fuera), MAX_MOSTRABLES)
        fuera = fuera[:MAX_MOSTRABLES]
    return fuera


def armar(mostrables: list[str]) -> dict:
    """El `response_format` para la llamada final.

    ⚠️ Si no hay nada para mostrar (una pregunta que no usó herramientas, o una
    herramienta que sólo devolvió un error), `mostrar` se declara como una lista
    que sólo puede estar VACÍA. No se saca el campo: el esquema es `strict`, así
    que sacarlo cambiaría la forma según el turno y la pantalla tendría que
    contemplar dos. Mejor un campo que siempre está y a veces está vacío.
    """
    mostrar = ({"type": "array", "items": {"enum": mostrables}} if mostrables
               else {"type": "array", "items": {"type": "string"}, "maxItems": 0})
    return {
        "type": "json_schema",
        "json_schema": {
            "name": NOMBRE,
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "respuesta": {
                        "type": "string",
                        "description": ("Tu respuesta en castellano, corta y "
                                        "concreta. SIN markdown: ni asteriscos "
                                        "ni guiones de lista."),
                    },
                    "mostrar": {
                        **mostrar,
                        "description": ("Qué partes del resultado tiene que "
                                        "dibujar la pantalla, como tablas. NO "
                                        "copies esos datos en `respuesta`: "
                                        "nombralos acá y se dibujan solos."),
                    },
                    "falta": {
                        "type": ["string", "null"],
                        "description": ("Qué NO pudiste contestar y por qué, o "
                                        "null. Si te pidieron algo que ninguna "
                                        "herramienta devuelve, va acá — no lo "
                                        "calcules."),
                    },
                },
                "required": ["respuesta", "mostrar", "falta"],
                "additionalProperties": False,
            },
        },
    }


def leer(texto: str | None, mostrables: list[str]) -> dict:
    """Convierte lo que contestó el modelo en `{respuesta, mostrar, falta, aviso}`.

    ⚠️ **NUNCA LEVANTA, Y NUNCA DEVUELVE VACÍO SI HAY TEXTO.** El esquema lo
    fuerza el proveedor, pero esta función también corre cuando el proveedor NO
    lo soporta y contestó prosa. En ese caso la prosa ES la respuesta: tratarla
    como un JSON roto sería tirar una contestación buena por una capacidad que
    ese proveedor no tiene.

    El `aviso` es el cinturón del enum: si el modelo igual nombró un campo que
    no existe, no se rompe la respuesta — se ignora ese nombre y se dice. Y eso
    no es sólo prolijidad: un nombre que el modelo quiso mostrar y no existe te
    está diciendo QUÉ LE FALTA A LA HERRAMIENTA. Es lo mismo que pasó con
    `por_mes`, que lo encontró el control de números.
    """
    crudo = (texto or "").strip()
    if not crudo:
        return {"respuesta": None, "mostrar": [], "falta": None, "aviso": None}
    try:
        d = json.loads(crudo)
        if not isinstance(d, dict) or "respuesta" not in d:
            raise ValueError("no tiene la forma esperada")
    except (ValueError, TypeError):
        # No es JSON: es prosa. El proveedor no soporta esquema, o lo ignoró.
        return {"respuesta": crudo, "mostrar": [], "falta": None, "aviso": None}

    pedidos = [str(m) for m in (d.get("mostrar") or []) if m]
    validos = [m for m in pedidos if m in mostrables]
    invalidos = [m for m in pedidos if m not in mostrables]
    return {
        "respuesta": d.get("respuesta") or None,
        "mostrar": validos,
        "falta": d.get("falta") or None,
        "aviso": (f"el modelo quiso mostrar {', '.join(invalidos)} y esa "
                  f"herramienta no lo devuelve") if invalidos else None,
    }
