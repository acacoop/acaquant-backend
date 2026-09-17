"""El grafo del asistente (LangGraph): ruteo → agentes en paralelo → junta.
Cada agente (`agentes/<nombre>.py`) corre su propio bucle modelo ↔ herramientas
como subgrafo. Arquitectura: docs/AvAgentAI.md."""
from __future__ import annotations

import json
import logging
import operator
import re
import uuid
from datetime import date
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Send

from asistente import agente as AGT
from asistente import control as CTL
from asistente import ejecutor as EXE
from asistente import esquema as ESQ
from asistente import estado as EST
from asistente import eventos as EV
from asistente import herramientas as H
from asistente import junta as JU
from asistente import memoria, pantalla
from asistente import ruteo as RUT
from asistente.agente import Agente
from asistente.agentes import AGENTES
from core import modelos
from core.traza import Traza

logger = logging.getLogger(__name__)

MAX_VUELTAS = 6
MAX_RESULTADO_CHARS = 20_000

_SESION_RE = re.compile(r"[0-9a-f]{32}")


def _unir(a: dict, b: dict) -> dict:
    return {**(a or {}), **(b or {})}


# ── el subgrafo de un agente: modelo ↔ herramientas ──────────────────────────


class EstadoAgente(TypedDict, total=False):
    mensajes: Annotated[list[BaseMessage], add_messages]
    foco: dict[str, str]
    pregunta: str
    usuario: str
    rol: str
    portal: str
    run_id: str
    cuentas: tuple[str, ...]
    sesion: str
    vueltas: Annotated[int, operator.add]
    tokens_in: Annotated[int, operator.add]
    tokens_out: Annotated[int, operator.add]
    llamadas: Annotated[list[int], operator.add]
    eventos: Annotated[list[dict], operator.add]
    datos: Annotated[list[dict], operator.add]
    evidencias: Annotated[list[dict], operator.add]
    error: str | None
    respuesta: str | None
    falta: str | None
    crudo: str | None


def _evento(agente: Agente, tipo: str, **datos) -> dict:
    return EV.emitir({"tipo": tipo, "agente": agente.nombre, **datos})


NO_SALE = (KeyError, modelos.SinClave)


def _modelo(tarea: str, s: dict, traza: Traza, *, esquema: dict | None = None):
    """El modelo de una tarea, con su traza. Levanta `NO_SALE` si no puede salir."""
    return modelos.modelo(tarea, usuario=s.get("usuario"), sesion=s.get("sesion"),
                          esquema=esquema, traza=traza)


def _traza(tarea: str, s: dict) -> Traza:
    """La traza de una llamada de esta tarea. Dato personal: sin extracto de texto."""
    t = modelos.resolver(tarea)
    return Traza(t.nombre, t.modelo, usuario=s.get("usuario"), sesion=s.get("sesion"),
                 run_id=s.get("run_id"),
                 guardar_texto=not t.traza_sin_texto)


def _modelo_de(agente: Agente, s: dict, traza: Traza, *, esquema: dict | None = None):
    """El modelo de un agente, con sus herramientas atadas. El esquema y las
    herramientas se excluyen (ver `esquema.py`): con herramientas, el esquema
    que pida el que llama se ignora."""
    con_tools = bool(agente.herramientas)
    m = _modelo(agente.tarea, s, traza, esquema=None if con_tools else esquema)
    if con_tools:
        m = m.bind_tools([H.como_tool(f) for f in agente.herramientas])
    return m


def subgrafo(agente: Agente):
    """El bucle de un agente como grafo: `modelo` decide, `herramientas` ejecuta."""

    def modelo(s: EstadoAgente) -> dict:
        vuelta = s.get("vueltas", 0) + 1
        eventos = [_evento(agente, "vuelta", n=vuelta)]
        entrada = [SystemMessage(content=AGT.sistema(agente.instruccion, s.get("foco") or {}))] + list(s["mensajes"])
        try:
            tr = _traza(agente.tarea, s)
            msg = _modelo_de(agente, s, tr, esquema=ESQ.FORMATO).invoke(entrada)
        except NO_SALE as e:
            eventos.append(_evento(agente, "corte", motivo=f"la llamada no salió: {e}"))
            return {"vueltas": 1, "eventos": eventos, "error": f"No se pudo llamar al modelo: {e}"}
        except Exception as e:
            eventos.append(_evento(agente, "corte", motivo=f"el proveedor falló: {e}"))
            return {"vueltas": 1, "eventos": eventos, "error": f"El proveedor no contestó: {e}"}
        uso = msg.usage_metadata or {}
        salida: dict = {
            "mensajes": [msg], "vueltas": 1, "eventos": eventos,
            "tokens_in": uso.get("input_tokens", 0), "tokens_out": uso.get("output_tokens", 0),
            "llamadas": list(tr.ids),
        }
        if not msg.tool_calls and not msg.invalid_tool_calls:
            leido = ESQ.leer(memoria.texto(msg.content))
            salida.update({"respuesta": leido["respuesta"], "falta": leido["falta"],
                           "crudo": memoria.texto(msg.content)})
            eventos.append(_evento(agente, "texto", texto=leido["respuesta"]))
        return salida

    def herramientas(s: EstadoAgente) -> dict:
        ultimo = s["mensajes"][-1]
        foco = dict(s.get("foco") or {})
        eventos, datos, nuevos, evidencias = [], [], [], []
        pedidos = [(tc["id"], tc["name"], tc["args"]) for tc in ultimo.tool_calls]
        pedidos += [(tc["id"], tc["name"], None) for tc in ultimo.invalid_tool_calls]
        for cid, nombre, args in pedidos:
            eventos.append(_evento(agente, "pide", herramienta=nombre, argumentos=args))
            contexto = EXE.RunContext(
                run_id=s.get("run_id") or s["sesion"], usuario=s.get("usuario") or "",
                rol=s.get("rol") or "admin", portal=s.get("portal") or "trading",
                cuentas=tuple(s.get("cuentas") or ()),
            )
            ejecucion = _ejecutar_detalle(agente, nombre, args, contexto)
            resultado = ejecucion.resultado
            evidencias.extend(ejecucion.evidencias)
            eventos.append(_evento(
                agente, "resultado", herramienta=nombre, resultado=resultado,
                acceso=ejecucion.acceso.value if ejecucion.acceso else None,
                duracion_ms=ejecucion.duracion_ms))
            if agente.foco:
                nuevo = EST.aprender(foco, args, resultado, claves=agente.foco)
                if nuevo != foco:
                    eventos.append(_evento(agente, "estado", estado=nuevo, antes=foco))
                    foco = nuevo
            limpio = H.para_el_modelo(resultado)
            datos.append({"herramienta": nombre, "argumentos": args, "resultado": limpio})
            nuevos.append(ToolMessage(
                tool_call_id=cid or "",
                content=json.dumps(limpio, ensure_ascii=False, default=str)[:MAX_RESULTADO_CHARS]))
        return {"mensajes": nuevos, "foco": foco, "eventos": eventos, "datos": datos,
            "evidencias": evidencias}

    def siguiente(s: EstadoAgente) -> str:
        if s.get("error"):
            return END
        ultimo = s["mensajes"][-1]
        if isinstance(ultimo, AIMessage) and (ultimo.tool_calls or ultimo.invalid_tool_calls):
            if s.get("vueltas", 0) >= MAX_VUELTAS:
                return "tope"
            return "herramientas"
        return END

    def tope(s: EstadoAgente) -> dict:
        """Se cortó con pedidos pendientes: cada uno se cierra con un `tool` de
        error, así el historial que se exporta nunca deja un `assistant` con
        `tool_calls` sin responder (el proveedor rechaza esa conversación)."""
        ultimo = s["mensajes"][-1]
        pendientes = [tc["id"] for tc in ultimo.tool_calls] + [tc["id"] for tc in ultimo.invalid_tool_calls]
        cierre = json.dumps({"error": f"no se ejecutó: se llegó al tope de {MAX_VUELTAS} vueltas"},
                            ensure_ascii=False)
        return {"mensajes": [ToolMessage(tool_call_id=cid or "", content=cierre) for cid in pendientes],
                "error": f"Di {MAX_VUELTAS} vueltas pidiendo herramientas y no llegué a una "
                         "respuesta. Probá con una pregunta más acotada.",
                "eventos": [_evento(agente, "corte",
                                    motivo=f"llegué a {MAX_VUELTAS} vueltas sin una respuesta")]}

    g = StateGraph(EstadoAgente)
    g.add_node("modelo", modelo)
    g.add_node("herramientas", herramientas)
    g.add_node("tope", tope)
    g.add_edge(START, "modelo")
    g.add_conditional_edges("modelo", siguiente, {"herramientas": "herramientas", "tope": "tope", END: END})
    g.add_edge("herramientas", "modelo")
    g.add_edge("tope", END)
    return g.compile()


def _ejecutar_detalle(agente: Agente, nombre: str, args: dict | None,
                      contexto: EXE.RunContext) -> EXE.ResultadoTool:
    return EXE.ejecutar(agente, nombre, args, contexto)


def _ejecutar(agente: Agente, nombre: str, args: dict | None,
              contexto: EXE.RunContext | None = None) -> dict:
    """Corre una herramienta del agente. Todo lo que sale mal vuelve como dato."""
    contexto = contexto or EXE.RunContext.actual(usuario="interno@acaquant")
    return _ejecutar_detalle(agente, nombre, args, contexto).resultado


# ── el grafo principal: ruteo → agentes → junta ──────────────────────────────


class Estado(TypedDict, total=False):
    pregunta: str
    usuario: str
    rol: str
    portal: str
    run_id: str
    cuentas: tuple[str, ...]
    sesion: str
    historial: list[dict]
    foco: Annotated[dict[str, str], _unir]
    agentes: list[str]
    salidas: Annotated[dict[str, dict], _unir]
    eventos: Annotated[list[dict], operator.add]
    vueltas: Annotated[int, operator.add]
    tokens_in: Annotated[int, operator.add]
    tokens_out: Annotated[int, operator.add]
    llamadas: Annotated[list[int], operator.add]
    evidencias: Annotated[list[dict], operator.add]
    mensajes: list[dict]
    respuesta: str | None
    falta: str | None
    error: str | None
    control: dict


_SUBGRAFOS = {n: subgrafo(a) for n, a in AGENTES.items()}


def preparar(s: Estado) -> dict:
    historial, turnos, msgs = memoria.podar(s.get("historial") or [])
    historial, ahorro = memoria.achicar(historial)
    eventos = [EV.emitir({"tipo": "pregunta", "texto": s["pregunta"], "sesion": s["sesion"],
                          "herramientas": sorted(H.POR_NOMBRE)})]
    if turnos:
        eventos.append(EV.emitir({"tipo": "podado", "turnos": turnos, "mensajes": msgs}))
    if ahorro:
        eventos.append(EV.emitir({"tipo": "achicado", "chars": ahorro}))
    return {"historial": historial, "eventos": eventos}


def ruteo(s: Estado) -> dict:
    """Quién atiende la pregunta, en tres capas (`ruteo.py`): las reglas; si
    ninguna decide, el modelo entre los candidatos; si el modelo no se entiende,
    todos los candidatos (más caro, no más peligroso)."""
    d = RUT.por_reglas(s["pregunta"])
    if d is not None and d.tipo == "contesta":
        return {"agentes": [], "respuesta": d.respuesta, "falta": None, "error": None,
                "eventos": [_ev_ruteo([], d.motivo),
                            {"tipo": "texto", "agente": "ruteo", "texto": d.respuesta}]}
    if d is not None and d.tipo == "van":
        return {"agentes": list(d.agentes), "eventos": [_ev_ruteo(d.agentes, d.motivo)]}
    # Ninguna regla cerró: elige el modelo. Si una acotó los candidatos a una
    # familia, elige solo entre ellos; si no hubo regla, entre todos.
    candidatos = tuple(d.agentes) if d is not None else ()
    porque = f"{d.motivo} · " if d is not None else ""
    todos = list(candidatos) or list(AGENTES)
    try:
        tr = _traza(RUT.TAREA, s)
        entrada = [SystemMessage(content=RUT.instruccion(s.get("foco") or {}, candidatos or None)),
                   HumanMessage(content=s["pregunta"])]
        msg = _modelo(RUT.TAREA, s, tr).invoke(entrada)
        elegidos = RUT.leer_eleccion(memoria.texto(msg.content), candidatos or None)
        uso = msg.usage_metadata or {}
        extra = {"tokens_in": uso.get("input_tokens", 0), "tokens_out": uso.get("output_tokens", 0),
                 "llamadas": list(tr.ids)}
        motivo = porque + ("eligió el modelo" if elegidos else "no se entendió la elección: van todos")
    except Exception as e:
        elegidos, extra, motivo = [], {}, f"{porque}el ruteo falló ({e}): van todos"
    agentes = elegidos or todos
    return {"agentes": agentes, "vueltas": 1, "eventos": [_ev_ruteo(agentes, motivo)], **extra}


def _ev_ruteo(elegidos, motivo: str) -> dict:
    """El evento del ruteo: a quiénes les tocó y por qué. Siempre dice quién
    decidió (una regla, el modelo, o que no se entendió)."""
    return EV.emitir({"tipo": "ruteo", "agente": "ruteo", "elegidos": list(elegidos),
                      "motivo": motivo})


def a_agentes(s: Estado) -> list[Send] | str:
    """A cada agente elegido, en paralelo. Sin agentes (una regla ya contestó),
    directo a cerrar."""
    return [Send(m, s) for m in s["agentes"]] or "finalizar"


def nodo_agente(nombre: str):
    agente = AGENTES[nombre]

    def correr(s: Estado) -> dict:
        if not agente.herramientas:
            # Modelado pero sin herramientas: sin datos no hay respuesta. Se
            # dice con la misma forma que un agente que erró, sin llamar a nadie.
            texto = f"Todavía no puedo consultar {agente.nombre} ({agente.describe})"
            return {"salidas": {nombre: {"respuesta": None, "falta": texto, "error": texto,
                                         "crudo": None, "datos": [], "mensajes": [
                                             {"role": "assistant", "content": f"No pude contestar: {texto}"}]}},
                    "foco": {}, "eventos": [_evento(agente, "corte", motivo="agente sin herramientas")],
                    "vueltas": 0, "tokens_in": 0, "tokens_out": 0, "llamadas": []}
        # Cada agente ve del historial solo lo suyo y las preguntas: lo que
        # contestó otro agente (o la junta) puede traer datos que este proveedor
        # no tiene que recibir.
        propio = memoria.de_agente(s["historial"], nombre)
        r = _SUBGRAFOS[nombre].invoke({
            "mensajes": memoria.desde_dicts(propio) + [HumanMessage(content=s["pregunta"])],
            "foco": dict(s.get("foco") or {}),
            "pregunta": s["pregunta"], "usuario": s["usuario"], "sesion": s["sesion"],
            "rol": s.get("rol") or "admin", "portal": s.get("portal") or "trading",
            "run_id": s.get("run_id") or s["sesion"], "cuentas": tuple(s.get("cuentas") or ()),
            "vueltas": 0, "tokens_in": 0, "tokens_out": 0, "llamadas": [], "eventos": [], "datos": [],
            "evidencias": [],
        })
        # Lo nuevo de este agente, sin la pregunta (la agrega `finalizar`, una vez).
        nuevos = memoria.a_dicts(list(r["mensajes"])[len(propio) + 1:])
        # Un agente que no llegó a contestar deja igual su turno cerrado: si la
        # pregunta quedara sin respuesta en su historial, la próxima vez la
        # tomaría como pendiente y la contestaría de nuevo.
        if r.get("error") and not any(d.get("role") == "assistant" for d in nuevos):
            nuevos.append({"role": "assistant", "content": f"No pude contestar: {r['error']}"})
        return {
            "salidas": {nombre: {
                "respuesta": r.get("respuesta"), "falta": r.get("falta"),
                "error": r.get("error"), "crudo": r.get("crudo"),
                "datos": r.get("datos") or [], "mensajes": nuevos,
            }},
            "foco": (r.get("foco") or {}) if agente.foco else {},
            "eventos": r.get("eventos") or [],
            "vueltas": r.get("vueltas", 0),
            "tokens_in": r.get("tokens_in", 0), "tokens_out": r.get("tokens_out", 0),
            "llamadas": r.get("llamadas") or [],
            "evidencias": r.get("evidencias") or [],
        }

    return correr


def junta(s: Estado) -> dict:
    """Con un agente, su respuesta es la respuesta. Con varios, una llamada más
    redacta con los datos de todos delante."""
    salidas = s.get("salidas") or {}
    orden = [m for m in s["agentes"] if m in salidas]
    errores = [salidas[m]["error"] for m in orden if salidas[m].get("error")]
    if len(orden) == 1:
        u = salidas[orden[0]]
        return {"respuesta": u.get("respuesta"), "falta": u.get("falta"), "error": u.get("error")}
    if errores and len(errores) == len(orden):
        return {"respuesta": None, "falta": None, "error": errores[0]}
    partes = []
    for m in orden:
        u = salidas[m]
        dijo = u.get("respuesta") or (f"no contestó ({u['error']})" if u.get("error") else "(sin respuesta)")
        partes.append(f"## {m}\nrespuesta: {dijo}\n"
                      f"datos: {json.dumps(u.get('datos') or [], ensure_ascii=False, default=str)[:MAX_RESULTADO_CHARS]}")
    entrada = [SystemMessage(content=AGT.sistema(JU.instruccion, s.get("foco") or {})),
               HumanMessage(content=f"Pregunta: {s['pregunta']}\n\n" + "\n\n".join(partes))]
    try:
        tr = _traza(JU.TAREA, s)
        msg = _modelo(JU.TAREA, s, tr, esquema=ESQ.FORMATO).invoke(entrada)
    except Exception as e:
        return {"error": f"La junta no pudo redactar: {e}",
                "eventos": [{"tipo": "corte", "agente": "junta", "motivo": str(e)}]}
    leido = ESQ.leer(memoria.texto(msg.content))
    uso = msg.usage_metadata or {}
    return {"respuesta": leido["respuesta"], "falta": leido["falta"], "error": None,
            "vueltas": 1, "tokens_in": uso.get("input_tokens", 0), "tokens_out": uso.get("output_tokens", 0),
            "llamadas": list(tr.ids),
            "eventos": [EV.emitir({"tipo": "junta", "agente": "junta", "agentes": orden}),
                        EV.emitir({"tipo": "texto", "agente": "junta", "texto": leido["respuesta"]})],
            # Al historial va solo la respuesta de la junta: su entrada (los
            # datos de todos los agentes) no es un turno de la conversación.
            "salidas": {"junta": {"mensajes": memoria.a_dicts([msg]),
                                  "crudo": memoria.texto(msg.content)}}}


def finalizar(s: Estado) -> dict:
    salidas = s.get("salidas") or {}
    # La pregunta queda marcada con los agentes que la atendieron: un agente que
    # no la vio no tiene por qué recibirla después (y contestarla tarde).
    mensajes = list(s["historial"]) + [{"role": "user", "content": s["pregunta"],
                                        "agentes": list(s["agentes"])}]
    for m in [*s["agentes"], "junta"]:
        # Cada mensaje queda marcado con su agente; la junta corre como el agente
        # de su tarea. La marca no viaja al proveedor (`memoria.desde_dicts`).
        agente = JU.COMO if m == "junta" else m
        mensajes += [{**d, "agente": agente} for d in (salidas.get(m) or {}).get("mensajes") or []]
    if not s["agentes"] and s.get("respuesta"):
        # Contestó una regla del ruteo: queda en el historial de la persona y
        # de ningún agente (la marca no es de nadie).
        mensajes.append({"role": "assistant", "content": s["respuesta"], "agente": "ruteo"})
    # El texto que se revisa se excluye del contexto; el resto (todo lo que el
    # modelo vio, de todos los agentes) es la fuente.
    crudos = [u.get("crudo") for u in salidas.values() if u.get("crudo")]
    contexto = memoria.contexto(mensajes)
    for c in crudos:
        contexto = contexto.replace(c, "")
    # Lo DADO: lo que el sistema le entregó aparte de los resultados. Vive en el
    # SYSTEM, que el contexto excluye a propósito; sin esto el control acusaba
    # al modelo de inventar las cuentas que el sistema mismo le había listado.
    dado = " ".join([*(s.get("cuentas") or ()), date.today().isoformat()])
    # Lo MOSTRADO: las tablas que la pantalla dibuja en este mismo turno. Un
    # número que está ahí no exige cita: la persona tiene la fuente a la vista.
    tablas = [t for e in s.get("eventos") or [] if e.get("tipo") == "resultado"
              and (t := pantalla.para_dibujar(e.get("resultado"))) is not None]
    mostrado = json.dumps(tablas, ensure_ascii=False, default=str)
    control = CTL.revisar(s.get("respuesta"), contexto=contexto, pregunta=s["pregunta"],
                          evidencias=s.get("evidencias") or [], dado=dado, mostrado=mostrado)
    # Dos canales: la cita `[E:…]` es para el control y para el ciclo (queda en
    # `control.citas` y en el historial del modelo). A la persona le llega la
    # frase limpia: en un texto que alguien lee, la marca es ruido.
    return {"mensajes": mensajes, "control": control,
            "respuesta": CTL.sin_citas(s.get("respuesta")), "falta": CTL.sin_citas(s.get("falta"))}


def _armar(checkpointer=None):
    g = StateGraph(Estado)
    g.add_node("preparar", preparar)
    g.add_node("ruteo", ruteo)
    for nombre in AGENTES:
        g.add_node(nombre, nodo_agente(nombre))
    g.add_node("junta", junta)
    g.add_node("finalizar", finalizar)
    g.add_edge(START, "preparar")
    g.add_edge("preparar", "ruteo")
    g.add_conditional_edges("ruteo", a_agentes, [*AGENTES, "finalizar"])
    for nombre in AGENTES:
        g.add_edge(nombre, "junta")
    g.add_edge("junta", "finalizar")
    g.add_edge("finalizar", END)
    return g.compile(checkpointer=checkpointer)


GRAFO = _armar()


def sesion_valida(pedida: str | None) -> str:
    s = str(pedida or "").strip().lower()
    return s if _SESION_RE.fullmatch(s) else uuid.uuid4().hex


def preguntar(pregunta: str, *, usuario: str, historial: list[dict] | None = None,
              estado: dict | None = None, sesion: str | None = None,
              rol: str = "admin", portal: str = "trading", run_id: str | None = None,
              grafo_compilado=None) -> dict[str, Any]:
    """Una pregunta de punta a punta. Nunca levanta: los errores vuelven en `error`."""
    run = run_id or uuid.uuid4().hex
    entrada: Estado = {
        "pregunta": pregunta, "usuario": usuario, "sesion": sesion_valida(sesion),
        "rol": rol, "portal": portal, "run_id": run,
        "cuentas": tuple(EXE.RunContext.actual(run_id=run, usuario=usuario, rol=rol,
                                                portal=portal).cuentas),
        "historial": list(historial or []), "foco": EST.sanear(estado),
        "agentes": [], "salidas": {}, "eventos": [], "vueltas": 0,
        "tokens_in": 0, "tokens_out": 0, "llamadas": [], "evidencias": [],
    }
    try:
        ejecutable = grafo_compilado or GRAFO
        config = {"configurable": {"thread_id": run}}
        snapshot = ejecutable.get_state(config) if grafo_compilado is not None else None
        if snapshot and snapshot.values and not snapshot.next:
            r = snapshot.values
        else:
            r = ejecutable.invoke(None if snapshot and snapshot.next else entrada, config=config)
    except EV.CancelacionSolicitada:
        raise
    except Exception as e:
        logger.exception("asistente: el grafo reventó")
        return {**_salida(entrada), "error": f"El asistente falló: {type(e).__name__}: {e}",
                "mensajes": entrada["historial"]}
    return _salida(r)


def _salida(r: dict) -> dict:
    return {
        "respuesta": r.get("respuesta"),
        "falta": r.get("falta"),
        "error": r.get("error"),
        "vueltas": r.get("vueltas", 0),
        "tokens_in": r.get("tokens_in", 0),
        "tokens_out": r.get("tokens_out", 0),
        "llamadas": list(r.get("llamadas") or []),
        "control": r.get("control") or {"ok": True, "hallazgos": []},
        "mensajes": [m for m in (r.get("mensajes") or []) if m.get("role") != "system"],
        "estado": dict(r.get("foco") or {}),
        "sesion": r.get("sesion"),
        "agentes": list(r.get("agentes") or []),
        "eventos": list(r.get("eventos") or []),
        "evidencias": list(r.get("evidencias") or []),
        "run_id": r.get("run_id"),
    }
