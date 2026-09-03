"""`lab/langgraph/grafo.py` — EL GRAFO. Acá está TODO el concepto de LangGraph.

Son ~40 líneas de código y cuatro ideas. Si entendés estas cuatro, entendiste
el framework:

  1. EL ESTADO      un diccionario tipado que viaja por el grafo. Cada nodo
                    devuelve un pedazo y LangGraph lo mergea.
  2. LOS NODOS      funciones normales: `estado -> pedazo de estado`.
  3. LAS ARISTAS    quién sigue a quién. Si son CONDICIONALES, ahí está la
                    DECISIÓN — que es exactamente lo que al agente de hoy le
                    falta (su `_agenda()` es un `sorted()`, nadie elige).
  4. EL CHECKPOINT  el estado se guarda después de cada paso. Eso es la MEMORIA
                    DE TRABAJO: podés cortar, retomar, y meter un humano en el
                    medio sin perder nada.

EL DIBUJO
=========

                    ┌──────────────┐
        entrada ──► │    agente    │ ◄─────────────┐
                    │  (el modelo) │               │
                    └──────┬───────┘               │
                           │                       │
                  ¿pidió herramientas?             │
                     │            │                │
                    sí            no               │
                     │            │                │
              ┌──────▼──────┐     ▼                │
              │ herramientas│    FIN               │
              │ (las corre) │                      │
              └──────┬──────┘                      │
                     └─────────────────────────────┘

**Ese ciclo es el loop agéntico.** El modelo mira, decide si le falta
información, pide una herramienta, ve el resultado, y vuelve a decidir. No hay
un número de pasos fijo: para cuando deja de pedir cosas.
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from lab.langgraph.herramientas import HERRAMIENTAS
from lab.langgraph.veredicto import Veredicto

SISTEMA = """Sos el investigador del sistema TradingAV, una plataforma quant de
una mesa de capitales argentina. Cuando algo falla, tu trabajo es averiguar qué
pasó y proponer qué hacer.

REGLAS:
- **Leé, no adivines.** Si no lo viste con una herramienta, no lo afirmes.
- Juntá evidencia ANTES de concluir. Usá las herramientas todas las veces que
  haga falta; pedí varias juntas cuando sepas que las vas a necesitar.
- Muchas veces la causa no es un bug sino **dos procesos nuestros que se
  contradicen**. Mirá qué otra cosa corrió cerca del momento en que se rompió.
- Citá el archivo y la línea, o la tabla, de donde sacaste cada cosa.
- Castellano rioplatense, concreto, sin relleno."""

# Lo que se le pide al final, cuando ya juntó todo. Va aparte del SISTEMA
# porque son dos trabajos distintos: uno es investigar, el otro es redactar el
# veredicto — y mezclarlos hace que empiece a redactar antes de terminar de mirar.
REDACCION = """Con TODO lo que averiguaste, completá el veredicto.

- `que_paso` son HECHOS con fecha y hora, sin interpretación.
- `que_haria` es una acción concreta, no «revisar». Si además de lo que te
  preguntaron quedó algo pendiente, decilo igual: para eso te contrataron.
- `lo_que_no_se` NUNCA va vacío. Siempre hay algo que no miraste.
- `de_donde` son las tablas y los archivos con su línea. Sin fuente no vale."""


class Estado(TypedDict):
    """EL ESTADO. Sólo tiene una cosa: la conversación.

    ⚠️ `add_messages` es un **reducer**: le dice a LangGraph cómo combinar lo
    que devuelve un nodo con lo que ya había. Sin él, cada nodo PISARÍA la
    conversación entera en vez de agregarle un mensaje. Es el detalle que más
    confunde al principio y es la mitad de por qué el framework existe.
    """
    messages: Annotated[list, add_messages]
    # El resultado final, ya con forma. Es lo que dibuja la pantalla — y por eso
    # NO se deriva del último mensaje: derivarlo sería adivinar dónde termina la
    # investigación y dónde empieza la conclusión.
    veredicto: Veredicto | None


def construir(modelo, con_memoria: bool = True):
    """Arma el grafo. `modelo` es cualquier cosa que sepa `.bind_tools()`."""
    cerebro = modelo.bind_tools(HERRAMIENTAS)

    def agente(estado: Estado) -> dict:
        """EL NODO QUE PIENSA. Recibe la conversación, devuelve UN mensaje.

        El mensaje puede ser la respuesta final, o un pedido de herramientas
        (`tool_calls`). El nodo no sabe cuál de las dos es: eso lo mira la
        arista condicional de abajo.
        """
        return {"messages": [cerebro.invoke([SystemMessage(SISTEMA)] + estado["messages"])]}

    def redactar(estado: Estado) -> dict:
        """EL NODO QUE CONCLUYE. Corre UNA vez, cuando el modelo dejó de pedir
        herramientas.

        `with_structured_output` no es un pedido amable: el modelo devuelve los
        campos o la llamada falla. Es la misma idea que el CHECK de la base que
        no deja guardar un hallazgo sin `que_hacer` — la forma la garantiza el
        sistema, no la buena voluntad de quien escribe.
        """
        # ⚠️ `method="function_calling"` NO es un detalle: LangChain por defecto
        # pide la respuesta estructurada con `response_format: json_schema`, y
        # DeepSeek contesta **HTTP 400 «This response_format type is
        # unavailable now»**. El mecanismo de HERRAMIENTAS, en cambio, le anda
        # perfecto — es el mismo que usa para pedir cada tool de la
        # investigación. O sea: se le pide el veredicto como si fuera una
        # herramienta más, que es lo que ya sabe hacer.
        #
        # Es el motivo por el que el proveedor vive en UN archivo: el dialecto
        # de cada uno es distinto y no se puede adivinar desde el grafo.
        escritor = modelo.with_structured_output(Veredicto,
                                                 method="function_calling")
        try:
            v = escritor.invoke([SystemMessage(SISTEMA)] + estado["messages"]
                                + [SystemMessage(REDACCION)])
        except Exception as e:
            # No se inventa un veredicto ni se esconde el fallo: se dice.
            return {"veredicto": None,
                    "messages": [AIMessage(f"no pude armar el veredicto: {e}")]}
        return {"veredicto": v}

    g = StateGraph(Estado)
    g.add_node("agente", agente)
    g.add_node("redactar", redactar)
    # `ToolNode` ejecuta las herramientas que el modelo pidió y devuelve un
    # ToolMessage por cada una. Es lo único "prefabricado" que usamos.
    g.add_node("herramientas", ToolNode(HERRAMIENTAS))

    g.add_edge(START, "agente")
    # ⚠️ LA ARISTA CONDICIONAL — acá vive la decisión. `tools_condition` mira el
    # último mensaje: si trae `tool_calls` manda a "herramientas", si no, al END.
    # Cuando el modelo deja de pedir herramientas NO se termina: se pasa a
    # concluir. Esa es la única diferencia con el grafo de antes, y es la que
    # convierte «una charla» en «una investigación con resultado».
    g.add_conditional_edges("agente", tools_condition,
                            {"tools": "herramientas", END: "redactar"})
    # Y la vuelta: lo que la herramienta devolvió entra de nuevo al modelo.
    # ESTA arista es el ciclo. Sin ella sería una cadena lineal, no un agente.
    g.add_edge("herramientas", "agente")
    g.add_edge("redactar", END)

    return g.compile(checkpointer=InMemorySaver() if con_memoria else None)
