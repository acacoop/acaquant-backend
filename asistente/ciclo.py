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

from asistente import control as CTL
from asistente import esquema as ESQ
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

# ── ACHICAR LA CONVERSACIÓN VIEJA ───────────────────────────────────────────
#
# ⚠️ **EL PROBLEMA, Y NO ES TEÓRICO.** El modelo no recuerda nada, así que en
# cada vuelta se le reenvía la conversación ENTERA. Un resultado de herramienta
# de 4.000 caracteres no se paga una vez: se paga en cada vuelta de esa pregunta
# Y en cada pregunta que venga después, mientras la pestaña siga abierta.
#
# **Por qué se puede tirar sin perder nada.** Mirá el orden de los mensajes:
#
#     1. usuario      «cuánta plata cobro»
#     2. modelo       pide cobros_futuros(805, 60)
#     3. herramienta  ← el JSON entero
#     4. modelo       «cobrás USD 611,83; septiembre 431,23; octubre 180,60…»
#
# El 4 ya contiene lo que importaba del 3: el modelo leyó el JSON, sacó lo que
# necesitaba y lo escribió. A partir de ahí el 3 es peso muerto.
#
# ⚠️⚠️ **SE ACHICA ENTRE PREGUNTAS, NUNCA DENTRO DE LA MISMA.** Adentro del turno
# el modelo necesita el resultado completo para contestar. Recién cuando esa
# pregunta terminó, el resultado pasa a ser historia. Por eso esto corre UNA vez,
# sobre el `historial` que llega de afuera, y no adentro del bucle.
#
# **Y es genérico a propósito**: el stub se arma con el nombre de la herramienta,
# sus argumentos y el tamaño de lo que se tira — tres cosas que el ciclo ya
# tiene. Una herramienta que se agregue mañana queda achicada sola, sin escribir
# una línea. Igual que la ficha del modelo, que sale de la función y no de una
# lista paralela.
#
# ── LAS PERILLAS. Son estas tres y están acá para que se muevan acá. ──
#
# Cuántos resultados recientes se dejan ENTEROS. 0 = se achican todos; 1 = el
# último queda completo, por si la próxima pregunta es sobre ese mismo dato.
RESULTADOS_ENTEROS = 1
# Por debajo de esto no vale la pena: el stub ocuparía casi lo mismo.
ACHICAR_DESDE_CHARS = 400
# Qué se le dice al modelo en lugar del resultado. Tiene que decir DOS cosas: que
# el dato existió (y con qué argumentos), y que puede volver a pedirlo. Sin la
# segunda, el modelo contesta «no tengo ese dato» en vez de llamar de nuevo.
PLANTILLA_ACHICADO = (
    "[resultado de {nombre}({args}) — ya usado en la respuesta de abajo; "
    "{chars} caracteres descartados para no reenviarlos. "
    "Si necesitás el detalle, volvé a llamar a la herramienta: "
    "además te va a llegar más fresco.]"
)


def _instruccion() -> str:
    """El SYSTEM completo: el bloque fijo de arriba MÁS las cuentas habilitadas.

    ── SU ROL EN EL CICLO: le evita al modelo una vuelta entera ──

    Sin esto, el modelo tenía que llamar una herramienta sólo para traducir «la
    805» a un `id_cuenta` válido, y de paso preguntaba. Con la lista delante no
    llama nada y no pregunta: contesta.

    ⚠️ **LO VARIABLE VA AL FINAL, Y ESE ES TODO EL PUNTO.** El proveedor cachea
    el PREFIJO del prompt; cualquier cosa que cambie rompe el caché de todo lo
    que viene después. Las cuentas cambian (se editan en el `.env`), el bloque
    de arriba no. Poniéndolas al final, el día que cambien se pierde el caché de
    tres renglones en vez del de la instrucción entera.

    Si no se pueden leer, se sigue sin ellas: el modelo va a preguntar, que es
    el comportamiento de antes. Una lista que no se pudo traer no puede dejar al
    asistente sin contestar.
    """
    try:
        r = H.cuentas_disponibles()
        filas = r.get("cuentas") or []
    except Exception as e:
        logger.warning("asistente: no pude listar las cuentas para el system (%s)", e)
        filas = []
    if not filas:
        return SYSTEM
    lista = "\n".join(f"  {c['id_cuenta']} — {c['nombre']}" for c in filas)
    return (f"{SYSTEM}\n"
            f"Cuentas habilitadas (son las ÚNICAS que podés consultar; el número "
            f"es el `cuenta` que llevan las herramientas):\n{lista}\n")


def _achicar(historial: list[dict]) -> tuple[list[dict], int]:
    """Reemplaza los resultados de herramienta VIEJOS por un resumen de una
    línea. Devuelve el historial nuevo y cuántos caracteres se ahorraron.

    ── SU ROL EN EL CICLO: baja lo que se paga en cada vuelta ──

    No toca los mensajes del usuario ni los del modelo: esos son la conversación
    y son chicos. Lo único que se achica es lo que devolvió una herramienta,
    que es lo único que puede pesar miles de caracteres.
    """
    # De atrás para adelante, para poder dejar enteros los N más recientes.
    quedan = RESULTADOS_ENTEROS
    salida, ahorro = [], 0
    for m in reversed(historial or []):
        if m.get("role") != "tool" or len(str(m.get("content") or "")) < ACHICAR_DESDE_CHARS:
            salida.append(m)
            continue
        if quedan > 0:
            quedan -= 1
            salida.append(m)
            continue
        crudo = str(m.get("content") or "")
        nombre, args = _quien_fue(historial, m.get("tool_call_id"))
        stub = PLANTILLA_ACHICADO.format(nombre=nombre, args=args, chars=len(crudo))
        ahorro += len(crudo) - len(stub)
        # ⚠️ `tool_call_id` se conserva TAL CUAL. El proveedor exige que cada
        # resultado conteste a un pedido suyo; si el id no calza, la llamada
        # entera se rechaza — y no por el contenido, que puede ser cualquiera.
        salida.append({**m, "content": stub})
    return list(reversed(salida)), ahorro


def _quien_fue(historial: list[dict], tool_call_id) -> tuple[str, str]:
    """Qué herramienta y con qué argumentos produjo ese resultado. Sale del
    mensaje del modelo que lo pidió, que está más arriba en la conversación.

    Si no se encuentra (un historial recortado a mano, por ejemplo), se degrada
    a «una herramienta»: el stub sigue sirviendo, sólo dice menos."""
    for m in historial or []:
        for p in m.get("tool_calls") or []:
            if p.get("id") == tool_call_id:
                fn = p.get("function") or {}
                return fn.get("name") or "una herramienta", str(fn.get("arguments") or "")[:200]
    return "una herramienta", ""


# ── LA INSTRUCCIÓN ──────────────────────────────────────────────────────────
#
# El primer mensaje de la conversación, con rol `system`: las reglas de la casa.
# Es lo primero que lee el modelo y **va en CADA llamada** — no recuerda nada,
# así que esto se reenvía entero en cada vuelta y en cada pregunta.
#
# Por eso va PRIMERO y es FIJO: el proveedor cachea el principio del prompt, y
# el principio es esto. Lo fijo adelante no es prolijidad, es dónde el caché
# puede pegar.
#
# Corta a propósito: cuantas más reglas tiene, más despareja las aplica — y las
# que se saltea no fallan, simplemente desaparecen de la respuesta. Cuando esto
# pase de unas 40 líneas, la señal no es «escribir mejor»: es que hace falta un
# segundo agente que revise, y eso es otra pieza.
#
# ⚠️⚠️ **Y REPETIR UNA REGLA LA VUELVE MÁS PESADA DE LO QUE QUERÉS** (visto en
# producción, 2026-09-13). «Preguntá de qué cuenta» estaba escrito en tres
# lugares —acá y en dos docstrings— y el modelo pidió confirmación de una cuenta
# que el usuario YA había nombrado: un turno entero de más, 2.110 tokens, para
# no enterarse de nada. La regla está ahora en UN solo lado.
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

Si el usuario ya nombró una cuenta, usala. Si no nombró ninguna y hay más de
una habilitada, preguntale cuál quiere: no elijas vos.

No conviertas, redondees ni sumes números por tu cuenta. Reportá los que
devolvió la herramienta, con su moneda. Si todo el resultado está en UNA sola
moneda, decila una vez y no en cada renglón.
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

    # ⚠️ Acá, y en ningún otro lado: lo viejo se achica ANTES de empezar. De acá
    # para abajo el bucle trabaja con la conversación completa de ESTA pregunta.
    historial, ahorro = _achicar(historial)

    mensajes = [{"role": "system", "content": _instruccion()}]
    mensajes += list(historial)
    mensajes.append({"role": "user", "content": pregunta})

    _ver("pregunta", texto=pregunta, herramientas=sorted(H.POR_NOMBRE))
    if ahorro:
        _ver("achicado", chars=ahorro)
    tokens_in = tokens_out = 0
    llamadas: list[int] = []

    for vuelta in range(1, MAX_VUELTAS + 1):
        _ver("vuelta", n=vuelta)
        # ⚠️ El esquema viaja en TODAS las vueltas: el proveedor entiende que un
        # pedido de herramienta no es «la respuesta final» y no le exige la
        # forma (medido en `scripts/diag_structured_output.py`). Si no lo
        # soporta, `ai.conversar` lo descarta y la respuesta llega como prosa.

        # ── PASO 1: se manda la conversación entera + las herramientas ──
        # Entera, sí: el modelo no recuerda nada de la vuelta anterior. Cada
        # llamada le reenvía todo desde el principio.
        r, llamada_id = ai.conversar(TAREA, mensajes=mensajes, herramientas=H.FICHAS,
                                   usuario=usuario, detalle=pregunta,
                                   formato=ESQ.FORMATO)
        if llamada_id:
            llamadas.append(llamada_id)

        if r is None:
            # El gateway se negó: sin clave del proveedor, o ruteo inseguro.
            _ver("corte", motivo="el gateway no dejó salir la llamada")
            return _salida(None, mensajes, vuelta, tokens_in, tokens_out, llamadas,
                           pregunta=pregunta, error="No se pudo llamar al modelo: falta la clave del "
                                 "proveedor, o el ruteo no es seguro para datos del negocio.")
        tokens_in += r.tokens_in or 0
        tokens_out += r.tokens_out or 0
        if not r.ok:
            _ver("corte", motivo=f"el proveedor falló: {r.error}")
            return _salida(None, mensajes, vuelta, tokens_in, tokens_out, llamadas,
                           pregunta=pregunta, error=f"El proveedor no contestó: {r.error}")

        # ── PASO 2: ¿terminó, o quiere una herramienta? ──
        if not r.pedidos:
            # ⚠️ El crudo va al historial TAL CUAL (JSON incluido): es lo que el
            # proveedor espera recibir de vuelta. Lo que se parsea es lo que va
            # a la pantalla, no lo que vuelve a la conversación.
            mensajes.append({"role": "assistant", "content": r.texto or ""})
            leido = ESQ.leer(r.texto)
            _ver("texto", texto=leido["respuesta"])
            # ⚠️ `crudo` es lo que se escribió en `mensajes`, que CON ESQUEMA no
            # es lo mismo que la respuesta: es el JSON que la contiene. Ver
            # `_salida`.
            return _salida(leido["respuesta"], mensajes, vuelta, tokens_in,
                           tokens_out, llamadas, pregunta=pregunta, extra=leido,
                           crudo=r.texto or "")

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
    return _salida(None, mensajes, MAX_VUELTAS, tokens_in, tokens_out, llamadas,
                   pregunta=pregunta, error=f"Di {MAX_VUELTAS} vueltas pidiendo herramientas y no llegué "
                         "a una respuesta. Probá con una pregunta más acotada.")


def _contexto(mensajes: list[dict], excluir: str | None) -> str:
    """Todo lo que el modelo tuvo delante, como un solo texto.

    ── SU ROL: es la FUENTE contra la que se revisa la respuesta ──

    Va todo: los resultados de las herramientas de este turno, los de las
    preguntas anteriores que sigan en el historial, y lo que el propio modelo
    escribió antes. Si un número está en cualquiera de esos lados, no lo inventó
    ahora — lo copió, que es lo que queremos.

    ⚠️⚠️ **SE EXCLUYE LA RESPUESTA QUE SE ESTÁ REVISANDO**, y no es un detalle:
    el ciclo la agrega a `mensajes` antes de devolverla, así que sin este filtro
    cada número se validaría CONTRA SÍ MISMO y el control no encontraría nada
    jamás — pasando en verde para siempre, que es la peor forma de no funcionar.
    """
    partes = []
    for m in mensajes:
        if excluir is not None and m.get("content") == excluir:
            continue
        if m.get("content") and m.get("role") != "system":
            partes.append(str(m["content"]))
        for p in m.get("tool_calls") or []:
            partes.append(str((p.get("function") or {}).get("arguments") or ""))
    return "\n".join(partes)


def _salida(texto, mensajes, vueltas, tokens_in, tokens_out, llamadas, error=None,
            pregunta: str = "", extra: dict | None = None,
            crudo: str | None = None) -> dict:
    """Lo que devuelve una pregunta. `mensajes` (sin el system, que se agrega
    solo) es lo que hay que pasar como `historial` en la pregunta siguiente.

    ⚠️⚠️ **`crudo` ES LO QUE SE ESCRIBIÓ EN `mensajes`, Y CON ESQUEMA NO ES LA
    RESPUESTA.** Al historial va el JSON tal cual —es lo que el proveedor espera
    recibir de vuelta—, así que la prosa viaja ADENTRO de ese JSON. El control
    excluye del contexto la respuesta que está revisando; si se le pasara el
    texto parseado, no encontraría ese string en ningún mensaje, **el JSON
    quedaría en el contexto y cada número se validaría contra sí mismo**: el
    control pasaría en verde para siempre, que es la peor forma de no funcionar.
    Es exactamente el bug que se arregló al nacer el control, y el structured
    output lo revivía por la puerta de atrás.

    Sin esquema los dos son el mismo string y `crudo` no cambia nada.
    """
    return {
        "respuesta": texto,
        "error": error,
        "vueltas": vueltas,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "llamadas": llamadas,
        # ⚠️ El veredicto viaja AL LADO de la respuesta, no en vez de ella: el
        # control avisa, no bloquea (`asistente/control.py`).
        "control": CTL.revisar(
            texto, contexto=_contexto(mensajes, crudo if crudo is not None else texto),
            pregunta=pregunta),
        "mensajes": [m for m in mensajes if m.get("role") != "system"],
        # Qué NO pudo contestar, cuando el proveedor soporta esquema. Sin este
        # renglón, una pregunta de dos partes contestada a medias se lee como
        # contestada entera.
        "falta": (extra or {}).get("falta"),
    }
