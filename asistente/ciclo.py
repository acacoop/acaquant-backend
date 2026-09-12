"""asistente/ciclo.py — EL CICLO. Acá pasa todo.

── QUÉ ES UN AGENTE, EN CUATRO LÍNEAS ──

    1. Se le manda la conversación MÁS la lista de herramientas que puede usar.
    2. El modelo contesta texto  → terminó.
       El modelo pide una herramienta → NO terminó.
    3. Si pidió: la corremos NOSOTROS, y le mandamos el resultado.
    4. Volver al 1.

Eso es todo. Un agente conversacional con herramientas es este bucle. Lo que
los frameworks llaman «Runner» es esta función.

⚠️ **EL MODELO NUNCA EJECUTA NADA.** Sólo pide, por nombre. Quien abre la base
de datos es el código de acá abajo. Esa separación es la que hace que se pueda
auditar: para saber todo lo que el asistente puede tocar, alcanza con leer
`asistente/herramientas.py`.

Lo que este archivo NO hace: no sabe de HTTP, no imprime nada por su cuenta, y
no sabe qué proveedor hay del otro lado. Para ver lo que va pasando se le pasa
`ver`, una función que recibe cada paso — ahí abajo está el detalle.

⚠️ **ESTO OCUPA EL LUGAR DEL INVESTIGADOR** (`docs/AGENT.md` §0.ff). Aquél eran
2.520 líneas sobre LangGraph que sólo se movían si alguien apretaba un botón que
nadie apretaba: se conectó a producción antes de que existiera un trabajo
esperándolo. La diferencia acá no es de tecnología —es más chico y hecho a
mano—: es que hay una pregunta real del otro lado antes de que esto exista.
"""
from __future__ import annotations

import json
import logging

from asistente import herramientas as H
from core import ai

logger = logging.getLogger(__name__)

TAREA = "asistente"

# ⚠️ **EL TECHO DE VUELTAS, Y NO ES OPCIONAL.** Un modelo que pide herramientas
# sin parar deja el bucle girando y cada vuelta es una llamada que se paga. Seis
# alcanzan de sobra para lo que hacemos: pedir dos o tres datos y contestar. Si
# alguna vez hace falta más, el número se sube ACÁ y se ve, que es distinto a no
# tenerlo.
MAX_VUELTAS = 6

# Cuánto texto de un resultado se le manda al modelo. Un resultado enorme entra
# entero en la próxima llamada y se paga por token, cada vuelta.
MAX_RESULTADO_CHARS = 20_000

# ── LA INSTRUCCIÓN ──────────────────────────────────────────────────────────
#
# Es lo primero que lee el modelo y va en cada llamada. Corta a propósito:
# cuantas más reglas tiene, más despareja las aplica — y las que se saltea no
# fallan, simplemente desaparecen de la respuesta. Cuando esto pase de unas 40
# líneas, la señal no es «escribir mejor»: es que hace falta un segundo agente
# que revise, y eso es otra pieza.
SYSTEM = """\
Sos el asistente de una mesa de renta fija argentina. Te habla el admin de la
plataforma. Contestás en castellano, corto y concreto.

TODO dato sale de las herramientas. Nunca inventes un ticker, una fecha, un
nominal ni una tasa: si no lo trajo una herramienta, no lo digas.

Si una herramienta devuelve un `error`, decilo con sus palabras y no lo tapes
con una estimación. «No pude mirar» es una respuesta válida.

Si un resultado trae la fecha de los datos, decila: los números son de esa
foto, no de este momento. Y si trae una lista de lo que quedó afuera,
nombrala — lo que no se pudo mirar no se omite.

Si hay varias cuentas habilitadas y el usuario no dijo cuál quiere, preguntale
antes de consultar. No elijas vos ni asumas que las quiere todas.

No conviertas, redondees ni sumes números por tu cuenta. Reportá los que
devolvió la herramienta, con su moneda.
"""


def _ejecutar(pedido: dict) -> dict:
    """Corre UNA herramienta de las que pidió el modelo.

    ── SU ROL EN EL CICLO: es el paso 3 ──

    Todo lo que puede salir mal vuelve como DATO, nunca como excepción: que el
    modelo pida una herramienta que no existe, que mande los argumentos rotos o
    que la función reviente. En los tres casos el modelo recibe un texto que
    explica qué pasó y puede corregir o decir que no pudo. Una excepción acá
    cortaría la conversación entera por algo que el modelo sabía arreglar.
    """
    nombre = pedido.get("nombre") or ""
    fn = H.POR_NOMBRE.get(nombre)
    if fn is None:
        return {"error": f"no existe una herramienta llamada {nombre!r}",
                "disponibles": sorted(H.POR_NOMBRE)}
    args = pedido.get("argumentos")
    if args is None:
        return {"error": "no pude leer tus argumentos: no son un JSON válido",
                "recibido": pedido.get("argumentos_crudos"),
                "que_hacer": "Volvé a pedir la herramienta con los argumentos bien armados."}
    try:
        return fn(**args)
    except TypeError as e:
        # Argumentos de más, de menos o con otro nombre.
        return {"error": f"los argumentos no coinciden con la herramienta: {e}"}
    except Exception as e:
        logger.warning("asistente: %s reventó (%s)", nombre, e)
        return {"error": f"la herramienta falló: {type(e).__name__}: {e}"}


def preguntar(
    pregunta: str,
    *,
    usuario: str,
    historial: list[dict] | None = None,
    ver=None,
) -> dict:
    """Una pregunta, de punta a punta. Devuelve la respuesta y qué pasó.

    ── SU ROL EN EL CICLO: ES el ciclo ──

    `historial` son los mensajes de las preguntas anteriores, para que se pueda
    seguir una conversación. En la respuesta viene `mensajes`, que es lo que hay
    que volver a pasar como `historial` la próxima vez.

    `ver` es una función que se llama en cada paso, con un diccionario que dice
    qué pasó. Es lo que los frameworks llaman «eventos». Acá no es una feature
    aparte: es el único lugar desde donde se puede mirar lo que hace el modelo,
    porque todo lo demás pasa adentro de esta función.

    ⚠️ NUNCA levanta una excepción. Si algo sale mal, viene en `error`.
    """
    def _ver(tipo: str, **datos):
        if ver:
            try:
                ver({"tipo": tipo, **datos})
            except Exception:
                pass  # mirar no puede romper lo que se está mirando

    mensajes = [{"role": "system", "content": SYSTEM}]
    mensajes += list(historial or [])
    mensajes.append({"role": "user", "content": pregunta})

    _ver("pregunta", texto=pregunta, herramientas=sorted(H.POR_NOMBRE))
    tokens_in = tokens_out = 0
    trazas: list[int] = []

    for vuelta in range(1, MAX_VUELTAS + 1):
        _ver("vuelta", n=vuelta)

        # ── PASO 1: se manda la conversación entera + las herramientas ──
        # Entera, sí: el modelo no recuerda nada de la vuelta anterior. Cada
        # llamada le reenvía todo desde el principio.
        r, traza_id = ai.conversar(TAREA, mensajes=mensajes, herramientas=H.FICHAS,
                                   usuario=usuario, detalle=pregunta)
        if traza_id:
            trazas.append(traza_id)

        if r is None:
            # El gateway se negó: sin clave, ruteo inseguro o sin presupuesto.
            _ver("corte", motivo="el gateway no dejó salir la llamada")
            return _salida(None, mensajes, vuelta, tokens_in, tokens_out, trazas,
                           error="No se pudo llamar al modelo: falta la clave del "
                                 "proveedor, o se agotó el presupuesto del día.")
        tokens_in += r.tokens_in or 0
        tokens_out += r.tokens_out or 0
        if not r.ok:
            _ver("corte", motivo=f"el proveedor falló: {r.error}")
            return _salida(None, mensajes, vuelta, tokens_in, tokens_out, trazas,
                           error=f"El proveedor no contestó: {r.error}")

        # ── PASO 2: ¿terminó, o quiere una herramienta? ──
        if not r.pedidos:
            _ver("texto", texto=r.texto)
            mensajes.append({"role": "assistant", "content": r.texto or ""})
            return _salida(r.texto, mensajes, vuelta, tokens_in, tokens_out, trazas)

        # ── PASO 3: pidió. Se le devuelve su propio mensaje TAL CUAL y, abajo,
        # un resultado por cada herramienta que pidió. El crudo va sin tocar:
        # rearmarlo a mano es cómo se rompe la conversación por un espacio.
        mensajes.append(r.mensaje)
        for p in r.pedidos:
            _ver("pide", herramienta=p.get("nombre"), argumentos=p.get("argumentos"))
            resultado = _ejecutar(p)
            _ver("resultado", herramienta=p.get("nombre"), resultado=resultado)
            mensajes.append({
                "role": "tool",
                "tool_call_id": p.get("id"),
                "content": json.dumps(resultado, ensure_ascii=False,
                                      default=str)[:MAX_RESULTADO_CHARS],
            })
        # ── PASO 4: y se vuelve a empezar.

    _ver("corte", motivo=f"llegué a {MAX_VUELTAS} vueltas sin una respuesta")
    return _salida(None, mensajes, MAX_VUELTAS, tokens_in, tokens_out, trazas,
                   error=f"Di {MAX_VUELTAS} vueltas pidiendo herramientas y no llegué "
                         "a una respuesta. Probá con una pregunta más acotada.")


def _salida(texto, mensajes, vueltas, tokens_in, tokens_out, trazas, error=None) -> dict:
    """Lo que devuelve una pregunta. `mensajes` (sin el system, que se agrega
    solo) es lo que hay que pasar como `historial` en la pregunta siguiente."""
    return {
        "respuesta": texto,
        "error": error,
        "vueltas": vueltas,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "trazas": trazas,
        "mensajes": [m for m in mensajes if m.get("role") != "system"],
    }
