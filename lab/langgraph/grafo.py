"""`lab/langgraph/grafo.py` — EL GRAFO. Cinco nodos y una regla que los ordena.

EL DIBUJO
=========

                     ┌──────────────┐
         caso ──────►│    agente    │◄──────────────┐
                     │  (el modelo) │               │
                     └──────┬───────┘               │
                    ¿pidió herramientas?            │
                       │            │               │
                      sí            no              │
                       │            │               │
              ┌────────▼─────┐      │               │
              │ herramientas │      │               │
              └────────┬─────┘      │               │
                       │            │               │
                 ┌─────▼─────┐      │               │
                 │  anotar   │──────┼───────────────┤   ← MEMORIA DE TRABAJO
                 └───────────┘      │               │
                                    │               │
                          ┌─────────▼────────┐      │
                          │  revisar el piso │      │   ← EL MÉTODO
                          └─────┬────────┬───┘      │
                          falta │        │ completo │
                                └────────┼──────────┘
                                         │
                                 ┌───────▼──────┐
                                 │   redactar   │
                                 └──────────────┘

LAS DOS PIEZAS QUE NO ESTÁN EN UN AGENTE CUALQUIERA
===================================================

**`revisar el piso` (el método).** Cuando el modelo deja de pedir herramientas,
NO concluye: se chequea que haya mirado el mínimo que su tipo de investigación
declara (`investigaciones.py`). Si falta algo, vuelve **con el faltante
nombrado**. Se verifica contra las herramientas que se ejecutaron de verdad, no
contra lo que el modelo dice que miró.

**`anotar` (la memoria de trabajo).** Después de cada tanda de herramientas se
anota, en una línea por herramienta, qué se pidió y qué volvió. Esa lista viaja
aparte de la conversación y se le vuelve a mostrar en cada vuelta. Sin eso el
modelo repite la misma búsqueda con el patrón apenas cambiado —pasó cinco veces
en una sola corrida—: la información estaba, pero enterrada entre quince
mensajes.
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from lab.langgraph.herramientas import HERRAMIENTAS
from lab.langgraph.investigaciones import INVESTIGACIONES
from lab.langgraph.veredicto import Veredicto

# Cuántas veces se lo puede mandar de vuelta por no haber cubierto el piso.
# ⚠️ Hay tope porque el piso puede ser IMPOSIBLE de cubrir en un caso concreto
# (un job que nunca corrió no deja corridas). Insistir para siempre sería un
# agente colgado; lo correcto es dejarlo concluir Y que el veredicto diga qué
# quedó sin mirar — que es justo para lo que existe `lo_que_no_se`.
MAX_VUELTAS = 2

SISTEMA = """Sos el investigador del sistema TradingAV, una plataforma quant de
una mesa de capitales argentina. Cuando algo falla, tu trabajo es averiguar qué
pasó y proponer qué hacer.

REGLAS:
- **Leé, no adivines.** Si no lo viste con una herramienta, no lo afirmes.
- Juntá evidencia ANTES de concluir. Pedí varias herramientas juntas cuando
  sepas que las vas a necesitar.
- Citá el archivo y la línea, o la tabla, de donde sacaste cada cosa.
- Castellano rioplatense, concreto, sin relleno."""

REDACCION = """Con TODO lo que averiguaste, completá el veredicto.

- `que_paso` son HECHOS con fecha y hora, sin interpretación.
- `que_haria` es una acción concreta, no «revisar». Si además de lo que te
  preguntaron quedó algo pendiente, decilo igual: para eso te contrataron.
- `lo_que_no_se` NUNCA va vacío. Siempre hay algo que no miraste.
- `de_donde` son las tablas y los archivos con su línea. Sin fuente no vale."""


class Estado(TypedDict):
    """Lo que viaja por el grafo.

    ⚠️ `add_messages` y `operator.add` son **reducers**: le dicen a LangGraph
    cómo COMBINAR lo que devuelve un nodo con lo que ya había. Sin ellos cada
    nodo pisaría la lista entera en vez de agregarle.
    """

    messages: Annotated[list, add_messages]
    investigacion: str                      # qué tipo de caso es
    intentos: Annotated[list[str], operator.add]   # LA MEMORIA DE TRABAJO
    faltan_del_piso: list[str]
    vueltas_piso: int
    veredicto: Veredicto | None


def _herramientas_usadas(mensajes: list) -> set[str]:
    """Las que se EJECUTARON. Se lee del rastro, no de lo que el modelo dijo."""
    return {m.name for m in mensajes if m.__class__.__name__ == "ToolMessage"}


def _ultima_tanda(mensajes: list) -> list[tuple[str, dict, str]]:
    """(herramienta, argumentos, resultado) de la última tanda ejecutada.

    Se recorre para atrás juntando `ToolMessage` hasta topar con el `AIMessage`
    que los pidió: ahí están los argumentos, que en el resultado no viajan.
    """
    resultados, i = {}, len(mensajes) - 1
    while i >= 0 and mensajes[i].__class__.__name__ == "ToolMessage":
        resultados[mensajes[i].tool_call_id] = str(mensajes[i].content)
        i -= 1
    if i < 0 or not getattr(mensajes[i], "tool_calls", None):
        return []
    return [(tc["name"], tc["args"], resultados.get(tc["id"], ""))
            for tc in mensajes[i].tool_calls]


def construir(modelo, con_memoria: bool = True):
    """Arma el grafo. `modelo` es cualquier cosa que sepa `.bind_tools()`."""
    cerebro = modelo.bind_tools(HERRAMIENTAS)

    def agente(estado: Estado) -> dict:
        """EL NODO QUE PIENSA. Devuelve UN mensaje: la conclusión, o un pedido
        de herramientas. Cuál de las dos es lo decide la arista de abajo."""
        inv = INVESTIGACIONES[estado["investigacion"]]
        contexto = [SystemMessage(SISTEMA)]
        if inv.ojo_con:
            contexto.append(SystemMessage(
                "OJO CON ESTO, que es específico de este tipo de caso:\n"
                + "\n".join(f"- {x}" for x in inv.ojo_con)))
        if estado.get("intentos"):
            # La memoria de trabajo, corta y aparte de la conversación.
            contexto.append(SystemMessage(
                "LO QUE YA MIRASTE (no lo repitas; si algo no te sirvió, "
                "probá por otro lado):\n" + "\n".join(estado["intentos"])))
        return {"messages": [cerebro.invoke(contexto + estado["messages"])]}

    def anotar(estado: Estado) -> dict:
        """LA MEMORIA DE TRABAJO: una línea por herramienta ejecutada."""
        lineas = []
        for nombre, args, salida in _ultima_tanda(estado["messages"]):
            arg = ", ".join(f"{v}" for v in args.values() if v) or "—"
            resumen = " ".join(str(salida).split())[:110]
            lineas.append(f"- {nombre}({arg[:60]}) → {resumen}")
        return {"intentos": lineas}

    def revisar_piso(estado: Estado) -> dict:
        """EL MÉTODO: ¿miró el mínimo que su tipo de investigación exige?"""
        inv = INVESTIGACIONES[estado["investigacion"]]
        faltan = inv.falta(_herramientas_usadas(estado["messages"]))
        vueltas = estado.get("vueltas_piso", 0)
        if not faltan or vueltas >= MAX_VUELTAS:
            return {"faltan_del_piso": faltan, "vueltas_piso": vueltas}
        # Vuelve, y se le NOMBRA lo que falta. Un «te falta algo» sin decir qué
        # lo haría adivinar, que es exactamente lo que vinimos a evitar.
        return {"faltan_del_piso": faltan, "vueltas_piso": vueltas + 1,
                "messages": [HumanMessage(
                    "Todavía no podés concluir: para este tipo de caso hay que "
                    "haber mirado " + ", ".join(f"`{h}`" for h in faltan)
                    + ". Usá esas herramientas y después concluí.")]}

    def _concluir_o_seguir(estado: Estado) -> str:
        return ("redactar" if not estado.get("faltan_del_piso")
                or estado.get("vueltas_piso", 0) >= MAX_VUELTAS else "agente")

    def redactar(estado: Estado) -> dict:
        """EL NODO QUE CONCLUYE. Corre UNA vez, con el piso ya cubierto.

        ⚠️ `method="function_calling"` NO es un detalle: LangChain por defecto
        pide la salida estructurada con `response_format: json_schema` y
        DeepSeek contesta **HTTP 400 «This response_format type is unavailable
        now»**. El mecanismo de HERRAMIENTAS sí le anda — es el mismo que usa
        para pedir cada tool—, así que se le pide el veredicto como si fuera
        una herramienta más. Es el motivo por el que el proveedor vive en UN
        archivo: el dialecto no se puede adivinar desde el grafo.
        """
        cierre = [SystemMessage(REDACCION)]
        if faltan := estado.get("faltan_del_piso"):
            # Si se agotaron las vueltas, el veredicto tiene que DECIRLO. Un
            # agente que concluye sin haber mirado y no lo aclara es el
            # invariante #1 roto.
            cierre.append(SystemMessage(
                "No llegaste a usar " + ", ".join(f"`{h}`" for h in faltan)
                + ". Decilo explícito en `lo_que_no_se`."))
        escritor = modelo.with_structured_output(Veredicto,
                                                 method="function_calling")
        try:
            v = escritor.invoke([SystemMessage(SISTEMA)] + estado["messages"]
                                + cierre)
        except Exception as e:
            # No se inventa un veredicto ni se esconde el fallo: se dice.
            return {"veredicto": None,
                    "messages": [AIMessage(f"no pude armar el veredicto: {e}")]}
        return {"veredicto": v}

    g = StateGraph(Estado)
    g.add_node("agente", agente)
    g.add_node("herramientas", ToolNode(HERRAMIENTAS))
    g.add_node("anotar", anotar)
    g.add_node("revisar_piso", revisar_piso)
    g.add_node("redactar", redactar)

    g.add_edge(START, "agente")
    # Cuando deja de pedir herramientas no termina: pasa a que le revisen si
    # miró lo mínimo. Esa es la diferencia entre una charla y una investigación.
    g.add_conditional_edges("agente", tools_condition,
                            {"tools": "herramientas", END: "revisar_piso"})
    g.add_edge("herramientas", "anotar")
    g.add_edge("anotar", "agente")
    g.add_conditional_edges("revisar_piso", _concluir_o_seguir,
                            {"agente": "agente", "redactar": "redactar"})
    g.add_edge("redactar", END)

    return g.compile(checkpointer=InMemorySaver() if con_memoria else None)
