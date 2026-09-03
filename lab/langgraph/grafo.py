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

from langchain_core.messages import SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from lab.langgraph.herramientas import HERRAMIENTAS

SISTEMA = """Sos un asistente que conoce el repo de TradingAV, una plataforma
quant de una mesa de capitales argentina. Te preguntan sobre el AV AGENT (el
subsistema de integridad que vigila motores, jobs, precios y datos).

REGLAS:
- **Leé, no adivines.** Si no lo viste con una herramienta, decí que no lo sabés.
- Usá las herramientas todas las veces que haga falta antes de contestar.
- Citá el archivo y la línea de donde sacaste cada cosa.
- Contestá en castellano rioplatense, corto y concreto."""


class Estado(TypedDict):
    """EL ESTADO. Sólo tiene una cosa: la conversación.

    ⚠️ `add_messages` es un **reducer**: le dice a LangGraph cómo combinar lo
    que devuelve un nodo con lo que ya había. Sin él, cada nodo PISARÍA la
    conversación entera en vez de agregarle un mensaje. Es el detalle que más
    confunde al principio y es la mitad de por qué el framework existe.
    """
    messages: Annotated[list, add_messages]


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

    g = StateGraph(Estado)
    g.add_node("agente", agente)
    # `ToolNode` ejecuta las herramientas que el modelo pidió y devuelve un
    # ToolMessage por cada una. Es lo único "prefabricado" que usamos.
    g.add_node("herramientas", ToolNode(HERRAMIENTAS))

    g.add_edge(START, "agente")
    # ⚠️ LA ARISTA CONDICIONAL — acá vive la decisión. `tools_condition` mira el
    # último mensaje: si trae `tool_calls` manda a "herramientas", si no, al END.
    g.add_conditional_edges("agente", tools_condition,
                            {"tools": "herramientas", END: END})
    # Y la vuelta: lo que la herramienta devolvió entra de nuevo al modelo.
    # ESTA arista es el ciclo. Sin ella sería una cadena lineal, no un agente.
    g.add_edge("herramientas", "agente")

    return g.compile(checkpointer=InMemorySaver() if con_memoria else None)
