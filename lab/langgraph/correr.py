"""`lab/langgraph/correr.py` — el CLI del laboratorio.

    python -m lab.langgraph.correr --guionado         # sin clave: prueba el cableado
    python -m lab.langgraph.correr "¿qué mira bono_sin_precio?"
    python -m lab.langgraph.correr                    # modo conversación

⚠️ Se corre desde la RAÍZ del repo (`python -m ...`), como todo acá.
"""
from __future__ import annotations

import argparse
import sys

from langchain_core.messages import AIMessage, HumanMessage

from lab.langgraph import grafo, modelo


def _mostrar(evento: dict) -> None:
    """Imprime lo que va pasando. **Ver los pasos es la mitad del ejercicio**:
    un agente que sólo muestra la respuesta final es imposible de depurar."""
    for _nodo, salida in evento.items():
        for m in salida.get("messages", []):
            if getattr(m, "tool_calls", None):
                for tc in m.tool_calls:
                    print(f"  🔧 pide  {tc['name']}({tc['args']})")
            elif m.__class__.__name__ == "ToolMessage":
                cuerpo = str(m.content).replace("\n", " ")[:160]
                print(f"  📄 {m.name} → {cuerpo}…")
            elif m.content:
                print(f"\n🤖 {m.content}\n")


# El guion del modo `--guionado`: dos turnos. Primero pide una herramienta,
# después contesta. Prueba el CICLO completo sin gastar un token.
_GUION = [
    AIMessage(content="", tool_calls=[
        {"name": "listar_habilidades", "args": {}, "id": "t1"}]),
    AIMessage(content="(modelo guionado) Leí el catálogo con la herramienta: "
                      "el cableado funciona — pidió una tool, se ejecutó de "
                      "verdad, y el resultado volvió al modelo."),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Laboratorio LangGraph sobre el repo")
    ap.add_argument("pregunta", nargs="*", help="una pregunta suelta")
    ap.add_argument("--guionado", action="store_true",
                    help="modelo de mentira: prueba el grafo sin clave ni costo")
    ap.add_argument("--modelo", default="", help=f"default {modelo.MODELO_DEFAULT}")
    a = ap.parse_args()

    cerebro = (modelo.guionado(_GUION) if a.guionado
               else modelo.real(a.modelo))
    app = grafo.construir(cerebro)
    # El `thread_id` es la CONVERSACIÓN: el checkpointer guarda el estado bajo
    # esa clave, y por eso el segundo mensaje se acuerda del primero.
    config = {"configurable": {"thread_id": "lab-1"}}

    def preguntar(texto: str) -> None:
        print(f"\n👤 {texto}")
        for evento in app.stream({"messages": [HumanMessage(texto)]},
                                 config, stream_mode="updates"):
            _mostrar(evento)

    if a.pregunta:
        preguntar(" ".join(a.pregunta))
        return 0
    if a.guionado:
        preguntar("¿qué habilidades tiene el agente?")
        return 0

    print("Escribí una pregunta (Ctrl-C para salir). El thread se mantiene: "
          "podés repreguntar sobre lo anterior.\n")
    while True:
        try:
            q = input("👤 ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if q:
            preguntar(q)


if __name__ == "__main__":
    sys.exit(main())
