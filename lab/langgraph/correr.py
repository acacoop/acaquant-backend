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
from lab.langgraph.veredicto import Veredicto, render


def _mostrar(evento: dict) -> None:
    """Imprime lo que va pasando. **Ver los pasos es la mitad del ejercicio**:
    un agente que sólo muestra la respuesta final es imposible de depurar —
    cuando contesta mal hay que poder distinguir si eligió mal la herramienta,
    si la herramienta devolvió basura, o si tenía todo bien y razonó mal."""
    for nodo, salida in evento.items():
        if (v := salida.get("veredicto")) is not None:
            print("\n" + render(v))
            continue
        for m in salida.get("messages", []):
            if getattr(m, "tool_calls", None):
                for tc in m.tool_calls:
                    print(f"  🔧 pide  {tc['name']}({tc['args']})")
            elif m.__class__.__name__ == "ToolMessage":
                cuerpo = str(m.content).replace("\n", " ")[:160]
                print(f"  📄 {m.name} → {cuerpo}…")
            elif m.content and nodo == "redactar":
                # La prosa del nodo `agente` NO se imprime: el veredicto la
                # reemplaza, y mostrar las dos deja al que lee sin saber cuál
                # manda. De `redactar` sólo sale texto cuando algo falló.
                print(f"\n⚠  {m.content}\n")


# El guion del modo `--guionado`: dos turnos. Primero pide una herramienta,
# después contesta. Prueba el CICLO completo sin gastar un token.
_GUION = [
    AIMessage(content="", tool_calls=[
        {"name": "listar_habilidades", "args": {}, "id": "t1"}]),
    AIMessage(content="(modelo guionado) el cableado funciona: pidió una "
                      "herramienta, se ejecutó de verdad, y el resultado "
                      "volvió al modelo."),
]


# El veredicto que devuelve el modo guionado. Existe para que la prueba gratis
# cubra TAMBIÉN el nodo que concluye, que es donde vive la forma de la respuesta.
_VEREDICTO_DE_PRUEBA = Veredicto(
    que_paso="(guionado) el grafo corrió entero: pidió una herramienta, se "
             "ejecutó, volvió al modelo y llegó hasta el nodo que concluye.",
    por_que="No hay causa que investigar: es una corrida de prueba sin modelo.",
    de_quien_es="no_se",
    que_haria="Nada. Para una investigación de verdad, correlo sin --guionado.",
    lo_que_no_se="Si el modelo elige bien las herramientas — eso el modo "
                 "guionado no lo puede probar, sólo el cableado.",
    de_donde=["lab/langgraph/correr.py::_VEREDICTO_DE_PRUEBA"])


def main() -> int:
    ap = argparse.ArgumentParser(description="Laboratorio LangGraph sobre el repo")
    ap.add_argument("pregunta", nargs="*", help="una pregunta suelta")
    ap.add_argument("--guionado", action="store_true",
                    help="modelo de mentira: prueba el grafo sin clave ni costo")
    ap.add_argument("--modelo", default="", help=f"default {modelo.MODELO_DEFAULT}")
    a = ap.parse_args()

    cerebro = (modelo.guionado(_GUION, veredicto=_VEREDICTO_DE_PRUEBA)
               if a.guionado else modelo.real(a.modelo))
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
