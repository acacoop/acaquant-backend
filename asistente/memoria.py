"""La memoria de una conversación: el historial que va y vuelve del navegador,
cómo se poda y se achica antes de cada pregunta, y la conversión entre el
dialecto del proveedor (dicts) y los mensajes de LangChain."""
from __future__ import annotations

import json

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

# Cuántos turnos completos (de `user` a `user`) se conservan.
TURNOS_QUE_QUEDAN = 8
# Techo de mensajes además del de turnos: un turno no tiene tamaño fijo.
MENSAJES_QUE_QUEDAN = 300
# Cuántos resultados de herramienta recientes quedan enteros; el resto se achica.
RESULTADOS_ENTEROS = 1
ACHICAR_DESDE_CHARS = 400
PLANTILLA_ACHICADO = (
    "[resultado de {nombre}({args}) — ya usado en la respuesta de abajo; "
    "{chars} caracteres descartados para no reenviarlos. "
    "Si necesitás el detalle, volvé a llamar a la herramienta: "
    "además te va a llegar más fresco.]"
)


def podar(historial: list[dict]) -> tuple[list[dict], int, int]:
    """Deja los últimos `TURNOS_QUE_QUEDAN` turnos y nunca más de
    `MENSAJES_QUE_QUEDAN` mensajes, tirando turnos enteros (un `tool` nunca
    queda sin el `assistant` que lo pidió). Devuelve (historial, turnos
    tirados, mensajes tirados)."""
    historial = list(historial or [])
    inicios = [i for i, m in enumerate(historial) if m.get("role") == "user"]
    if not inicios:
        return [], 0, len(historial)
    desde = max(0, len(inicios) - TURNOS_QUE_QUEDAN)
    while desde < len(inicios) - 1 and len(historial) - inicios[desde] > MENSAJES_QUE_QUEDAN:
        desde += 1
    corte = inicios[desde]
    if corte == 0:
        return historial, 0, 0
    return historial[corte:], desde, corte


def achicar(historial: list[dict]) -> tuple[list[dict], int]:
    """Reemplaza los resultados de herramienta viejos por un stub de una línea.
    El `tool_call_id` se conserva tal cual. Devuelve (historial, chars ahorrados)."""
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
        nombre, args = _quien_pidio(historial, m.get("tool_call_id"))
        stub = PLANTILLA_ACHICADO.format(nombre=nombre, args=args, chars=len(crudo))
        ahorro += len(crudo) - len(stub)
        salida.append({**m, "content": stub})
    return list(reversed(salida)), ahorro


def _quien_pidio(historial: list[dict], tool_call_id) -> tuple[str, str]:
    for m in historial or []:
        for p in m.get("tool_calls") or []:
            if p.get("id") == tool_call_id:
                fn = p.get("function") or {}
                return fn.get("name") or "una herramienta", str(fn.get("arguments") or "")[:200]
    return "una herramienta", ""


# ── dialecto del proveedor ⇄ mensajes de LangChain ──────────────────────────


def a_dicts(mensajes: list[BaseMessage]) -> list[dict]:
    """Mensajes de LangChain → dicts del proveedor. Un `AIMessage` que vino del
    proveedor vuelve tal cual (`additional_kwargs["crudo"]`)."""
    out: list[dict] = []
    for m in mensajes:
        if isinstance(m, SystemMessage):
            out.append({"role": "system", "content": texto(m.content)})
        elif isinstance(m, HumanMessage):
            out.append({"role": "user", "content": texto(m.content)})
        elif isinstance(m, ToolMessage):
            out.append({"role": "tool", "tool_call_id": m.tool_call_id,
                        "content": texto(m.content)})
        elif isinstance(m, AIMessage):
            crudo = m.additional_kwargs.get("crudo")
            if crudo:
                out.append(dict(crudo))
                continue
            d: dict = {"role": "assistant", "content": texto(m.content) or None}
            llamadas = [{
                "id": tc["id"], "type": "function",
                "function": {"name": tc["name"],
                             "arguments": json.dumps(tc["args"], ensure_ascii=False)},
            } for tc in m.tool_calls]
            # Un pedido con argumentos ilegibles también se exporta, tal cual:
            # tiene su `tool` de error y el par tiene que viajar completo.
            llamadas += [{
                "id": tc["id"], "type": "function",
                "function": {"name": tc["name"], "arguments": str(tc.get("args") or "")},
            } for tc in m.invalid_tool_calls]
            if llamadas:
                d["tool_calls"] = llamadas
            out.append(d)
    return out


def de_mundo(historial: list[dict], mundo: str) -> list[dict]:
    """Lo que un mundo puede ver del historial: las preguntas y lo marcado con
    su nombre. Lo que escribió otro mundo no le llega."""
    return [d for d in historial or []
            if d.get("role") == "user" or d.get("mundo") == mundo]


def desde_dicts(historial: list[dict]) -> list[BaseMessage]:
    """Dicts del proveedor (el historial que manda el navegador) → mensajes de
    LangChain. Los `system` no viajan en el historial: se ignoran. La marca
    `mundo` es del historial, no del proveedor: se saca."""
    out: list[BaseMessage] = []
    for d in historial or []:
        d = {k: v for k, v in d.items() if k != "mundo"}
        rol = d.get("role")
        if rol == "user":
            out.append(HumanMessage(content=str(d.get("content") or "")))
        elif rol == "tool":
            out.append(ToolMessage(content=str(d.get("content") or ""),
                                   tool_call_id=str(d.get("tool_call_id") or "")))
        elif rol == "assistant":
            calls = []
            for c in d.get("tool_calls") or []:
                fn = c.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (TypeError, ValueError):
                    args = {}
                calls.append({"name": fn.get("name") or "", "args": args if isinstance(args, dict) else {},
                              "id": c.get("id"), "type": "tool_call"})
            out.append(AIMessage(content=str(d.get("content") or ""), tool_calls=calls,
                                 additional_kwargs={"crudo": dict(d)}))
    return out


def contexto(mensajes: list[dict], excluir: str | None = None) -> str:
    """Todo lo que el modelo tuvo delante, como un solo texto, para el control.
    Excluye el `system` y el texto que se está revisando."""
    partes = []
    for m in mensajes:
        if m.get("role") == "system":
            continue
        if excluir is not None and m.get("content") == excluir:
            continue
        if m.get("content"):
            partes.append(str(m["content"]))
        for p in m.get("tool_calls") or []:
            partes.append(str((p.get("function") or {}).get("arguments") or ""))
    return "\n".join(partes)


def texto(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(c.get("text", "")) if isinstance(c, dict) else str(c) for c in content)
    return str(content or "")
