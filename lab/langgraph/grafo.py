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

import json
import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from lab.langgraph.herramientas import DETERMINISTAS, HERRAMIENTAS
from lab.langgraph.investigaciones import INVESTIGACIONES
from lab.langgraph.veredicto import Veredicto

# Cuántas veces se lo puede mandar de vuelta por no haber cubierto el piso.
# ⚠️ Hay tope porque el piso puede ser IMPOSIBLE de cubrir en un caso concreto
# (un job que nunca corrió no deja corridas). Insistir para siempre sería un
# agente colgado; lo correcto es dejarlo concluir Y que el veredicto diga qué
# quedó sin mirar — que es justo para lo que existe `lo_que_no_se`.
MAX_VUELTAS = 2

# Cuántas veces puede PENSAR en una investigación. Es un presupuesto duro, y
# existe porque sin él el grafo muere con `GraphRecursionError` — un traceback
# de Python en vez de una conclusión. Pasó: 18 búsquedas, ninguna respuesta.
#
# ⚠️ Agotarlo NO es un error: es un resultado. Se corta, se concluye igual, y el
# veredicto DICE que se cortó. Un agente que se queda sin margen y no lo aclara
# es el invariante #1 roto — «no terminé de mirar» disfrazado de conclusión.
MAX_PASOS = 12

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
    vueltas: int                            # cuántas veces pensó
    corto_por_presupuesto: bool
    veredicto: dict | None                  # datos planos, no el objeto


def _clave(tc: dict) -> str:
    """La identidad de un pedido: la herramienta MÁS sus argumentos.

    Con `sort_keys` para que `{a:1,b:2}` y `{b:2,a:1}` sean el mismo pedido —
    si no, el modelo esquivaría la deduplicación sin proponérselo, sólo por el
    orden en que armó el JSON.
    """
    return f"{tc['name']}({json.dumps(tc['args'], sort_keys=True, ensure_ascii=False)})"


def _ya_ejecutadas(mensajes: list) -> dict[str, str]:
    """Todo lo que YA se pidió y se ejecutó, con su resultado.

    Se reconstruye del rastro (los `tool_call_id` que tienen respuesta), no de
    una lista aparte: una lista aparte es otro estado que mantener sincronizado.
    """
    por_id = {m.tool_call_id: str(m.content) for m in mensajes
              if m.__class__.__name__ == "ToolMessage"}
    hechas = {}
    for m in mensajes:
        for tc in (getattr(m, "tool_calls", None) or []):
            if tc["id"] in por_id:
                hechas[_clave(tc)] = por_id[tc["id"]]
    return hechas


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

    ejecutor = ToolNode(HERRAMIENTAS)

    def herramientas(estado: Estado) -> dict:
        """EJECUTA lo que el modelo pidió — **salvo lo que ya pidió antes.**

        ⚠️ **ACÁ LA MEMORIA DEJA DE SER UN CONSEJO.** La versión anterior le
        mostraba una lista de «lo que ya miraste, no lo repitas» y el modelo la
        ignoraba: en una corrida real buscó `alta_bono` en `agente/` **cinco
        veces**, con el mismo resultado las cinco, hasta agotar el presupuesto
        del grafo. La lista llegaba (se verificó); simplemente no obligaba.
        Pedir por favor no alcanza — es la misma diferencia entre «confío en
        que no va a escribir» y «la base no lo deja».
        """
        pedidos = getattr(estado["messages"][-1], "tool_calls", None) or []
        previas = _ya_ejecutadas(estado["messages"][:-1])

        repetidos = {tc["id"]: previas[_clave(tc)] for tc in pedidos
                     if tc["name"] in DETERMINISTAS and _clave(tc) in previas}
        nuevos = [tc for tc in pedidos if tc["id"] not in repetidos]

        hechas = {}
        if nuevos:
            r = ejecutor.invoke({"messages": [AIMessage(content="",
                                                        tool_calls=nuevos)]})
            hechas = {m.tool_call_id: m for m in r["messages"]}

        # Se devuelven EN EL ORDEN PEDIDO: el proveedor exige una respuesta por
        # cada `tool_call`, y desordenarlas es pedir un 400 evitable.
        salidas = []
        for tc in pedidos:
            if tc["id"] in repetidos:
                salidas.append(ToolMessage(
                    content=("YA PEDISTE ESTO en esta investigación, y el repo "
                             "no cambia mientras investigás: da lo mismo. Esto "
                             "fue el resultado:\n" + repetidos[tc["id"]][:1500]
                             + "\n\nSi no te alcanzó, NO lo vuelvas a buscar: "
                             "abrí el archivo con `leer_archivo`."),
                    name=tc["name"], tool_call_id=tc["id"]))
            elif tc["id"] in hechas:
                salidas.append(hechas[tc["id"]])
        return {"messages": salidas}

    def cortar(estado: Estado) -> dict:
        """Se acabó el presupuesto de pasos con herramientas pedidas en el aire.

        ⚠️ Hay que contestarle a CADA `tool_call` aunque no se ejecute ninguna:
        una conversación con un pedido sin respuesta hace que el proveedor
        rechace la llamada siguiente —la del veredicto— con un 400. O sea que
        saltear esto convertiría el corte prolijo en una caída.
        """
        pedidos = getattr(estado["messages"][-1], "tool_calls", None) or []
        return {"corto_por_presupuesto": True,
                "messages": [ToolMessage(
                    content="NO se ejecutó: se agotó el presupuesto de pasos "
                            "de esta investigación. Concluí con lo que tengas.",
                    name=tc["name"], tool_call_id=tc["id"]) for tc in pedidos]}

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
        return {"messages": [cerebro.invoke(contexto + estado["messages"])],
                "vueltas": estado.get("vueltas", 0) + 1}

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

    def _despues_de_pensar(estado: Estado) -> str:
        """¿Ejecuto lo que pidió, corto por presupuesto, o paso a concluir?

        Reemplaza al `tools_condition` de LangGraph, que sólo mira si hay
        `tool_calls`. Acá hay una tercera salida —el presupuesto— y es la que
        evita que el grafo muera con un error de recursión en vez de concluir.
        """
        if not getattr(estado["messages"][-1], "tool_calls", None):
            return "revisar_piso"
        return "cortar" if estado.get("vueltas", 0) >= MAX_PASOS else "herramientas"

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
        if estado.get("corto_por_presupuesto"):
            cierre.append(SystemMessage(
                "Se agotó el presupuesto de pasos: te quedaste sin margen para "
                "seguir mirando. Decilo explícito en `lo_que_no_se`, con qué "
                "te faltaba averiguar."))
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
        return {"veredicto": v.model_dump()}

    g = StateGraph(Estado)
    g.add_node("agente", agente)
    g.add_node("herramientas", herramientas)
    g.add_node("cortar", cortar)
    g.add_node("anotar", anotar)
    g.add_node("revisar_piso", revisar_piso)
    g.add_node("redactar", redactar)

    g.add_edge(START, "agente")
    # Tres salidas después de pensar: ejecutar lo que pidió, cortar por
    # presupuesto, o pasar a que le revisen si miró lo mínimo.
    g.add_conditional_edges("agente", _despues_de_pensar,
                            {"herramientas": "herramientas", "cortar": "cortar",
                             "revisar_piso": "revisar_piso"})
    g.add_edge("cortar", "redactar")
    g.add_edge("herramientas", "anotar")
    g.add_edge("anotar", "agente")
    g.add_conditional_edges("revisar_piso", _concluir_o_seguir,
                            {"agente": "agente", "redactar": "redactar"})
    g.add_edge("redactar", END)

    return g.compile(checkpointer=InMemorySaver() if con_memoria else None)
