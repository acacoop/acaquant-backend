"""`lab/langgraph/correr.py` — el CLI del laboratorio.

    python -m lab.langgraph.correr --guionado                 # sin clave ni costo
    python -m lab.langgraph.correr reincidencia M31G6         # un caso, con método
    python -m lab.langgraph.correr job dolar_mep
    python -m lab.langgraph.correr libre "¿qué mira bono_sin_precio?"
    python -m lab.langgraph.correr --tipos                    # qué sabe investigar
    python -m lab.langgraph.correr --historial                # lo que ya se investigó
    python -m lab.langgraph.correr --sql                      # el SQL del diario

⚠️ **El caso entra por TIPO, no por prosa.** Un empleado no recibe una pregunta
redactada: recibe un caso de una cola. Así el método queda determinado sin
adivinar de qué se trata, y así va a funcionar el día que esto sea un botón
sobre una fila en la pantalla.

Se corre desde la RAÍZ del repo (`python -m ...`), como todo acá.
"""
from __future__ import annotations

import argparse
import sys

from langchain_core.messages import AIMessage, HumanMessage

from lab.langgraph import diario, grafo, modelo
from lab.langgraph.investigaciones import INVESTIGACIONES
from lab.langgraph.veredicto import Veredicto, render


def _mostrar(evento: dict) -> None:
    """Imprime lo que va pasando. **Ver los pasos es la mitad del ejercicio**:
    cuando contesta mal hay que poder distinguir si eligió mal la herramienta,
    si la herramienta devolvió basura, o si tenía todo y razonó mal."""
    for nodo, salida in evento.items():
        # ⚠️ Un nodo que no cambia nada del estado llega como `None`, no como
        # `{}`. Sin esta guarda, la primera investigación sin antecedentes
        # revienta con «NoneType has no attribute get».
        salida = salida or {}
        if g := salida.get("guardado"):
            print(f"  💾 guardado en el diario, id {g['id']}" if g.get("ok")
                  else f"  ⚠ NO se pudo guardar: {g.get('error', '')[:120]}")
            continue
        if (v := salida.get("veredicto")) is not None:
            print("\n" + render(v))
            continue
        for m in salida.get("messages", []):
            if getattr(m, "tool_calls", None):
                for tc in m.tool_calls:
                    print(f"  🔧 pide  {tc['name']}({tc['args']})")
            elif m.__class__.__name__ == "ToolMessage":
                print(f"  📄 {m.name} → {' '.join(str(m.content).split())[:150]}…")
            elif nodo == "antecedentes":
                print(f"\n  📚 {' '.join(str(m.content).split())[:400]}\n")
            elif nodo == "cortar":
                print("\n  ⏳ se agotó el presupuesto de pasos — concluyo con "
                      "lo que hay\n")
                break
            elif nodo == "revisar_piso":
                # El método frenándolo. Verlo es importante: es la diferencia
                # entre «se le ocurrió mirar eso» y «tuvo que mirarlo».
                print(f"\n  ⛔ {m.content}\n")
            elif m.content and nodo == "redactar":
                # De `redactar` sólo sale texto cuando algo falló. La prosa del
                # nodo `agente` no se imprime: el veredicto la reemplaza, y
                # mostrar las dos deja al que lee sin saber cuál manda.
                print(f"\n⚠  {m.content}\n")


# El veredicto del modo guionado: existe para que la prueba gratis cubra TAMBIÉN
# el nodo que concluye, que es donde vive la forma de la respuesta.
_VEREDICTO_DE_PRUEBA = Veredicto(
    que_paso="(guionado) el grafo corrió entero: pidió una herramienta, se "
             "ejecutó, se anotó en la memoria de trabajo, se revisó el piso y "
             "llegó al nodo que concluye.",
    por_que="No hay causa que investigar: es una corrida de prueba sin modelo.",
    de_quien_es="no_se",
    que_haria="Nada. Para una investigación de verdad, correlo sin --guionado.",
    lo_que_no_se="Si el modelo elige bien las herramientas — eso el modo "
                 "guionado no lo prueba, sólo el cableado.",
    de_donde=["lab/langgraph/correr.py::_VEREDICTO_DE_PRUEBA"])
# ⚠️ Queda como OBJETO, igual que lo que devuelve el modelo real: el que lo
# convierte a datos planos es el grafo. Si se convirtiera acá, el modo guionado
# probaría un camino que en producción no existe — y un modo de prueba que
# recorre otro camino no prueba nada.

_GUION = [
    AIMessage(content="", tool_calls=[
        {"name": "listar_habilidades", "args": {}, "id": "t1"}]),
    AIMessage(content="(guionado) listo."),
]


def _tipos() -> None:
    print("\nQué sabe investigar:\n")
    for n, i in INVESTIGACIONES.items():
        piso = ", ".join(i.piso) or "(sin piso)"
        print(f"  {n:16} {i.que_es}\n{'':18}piso: {piso}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Laboratorio LangGraph")
    ap.add_argument("tipo", nargs="?", default="",
                    help=f"uno de: {', '.join(INVESTIGACIONES)}")
    ap.add_argument("caso", nargs="*", help="el sujeto: un ticker, un job…")
    ap.add_argument("--guionado", action="store_true",
                    help="modelo de mentira: prueba el grafo sin clave ni costo")
    ap.add_argument("--tipos", action="store_true", help="qué sabe investigar")
    ap.add_argument("--historial", action="store_true",
                    help="lo que ya se investigó (filtra por tipo y caso si los das)")
    ap.add_argument("--sql", action="store_true",
                    help="imprime el SQL del diario, para correrlo una vez en Supabase")
    ap.add_argument("--modelo", default="", help=f"default {modelo.MODELO_DEFAULT}")
    a = ap.parse_args()

    if a.tipos:
        _tipos()
        return 0
    if a.sql:
        print(diario.SQL_ESQUEMA)
        return 0
    if a.historial:
        print("\n" + diario.historial(a.tipo, " ".join(a.caso)) + "\n")
        return 0

    tipo, caso = a.tipo or "libre", " ".join(a.caso)
    if not a.guionado:
        if tipo not in INVESTIGACIONES:
            print(f"«{tipo}» no es un tipo de investigación.")
            _tipos()
            return 1
        if not caso:
            print(f"Falta el caso. Ej: `python -m lab.langgraph.correr "
                  f"{tipo} M31G6`")
            return 1

    cerebro = (modelo.guionado(_GUION, veredicto=_VEREDICTO_DE_PRUEBA)
               if a.guionado else modelo.real(a.modelo))
    app = grafo.construir(cerebro)
    inv = INVESTIGACIONES[tipo if tipo in INVESTIGACIONES else "libre"]
    pregunta = inv.pregunta.format(caso=caso or "una prueba del cableado")

    print(f"\n[{inv.nombre}] {pregunta}\n")
    estado = {"messages": [HumanMessage(pregunta)], "investigacion": inv.nombre,
              "caso": caso, "intentos": [], "faltan_del_piso": [],
              "vueltas_piso": 0, "vueltas": 0, "corto_por_presupuesto": False,
              "veredicto": None, "guardado": {}}
    # El `thread_id` es la CONVERSACIÓN: el checkpointer guarda el estado bajo
    # esa clave, y por eso el grafo puede frenarse y retomar sin perder nada.
    # El límite de recursión de LangGraph es la RED, no el freno: el freno es
    # `MAX_PASOS`, que corta y concluye. Se deja holgado para que el que actúe
    # sea siempre el freno prolijo y no la red.
    # ⚠️ El `thread_id` lleva la HORA. Sin eso, correr el mismo caso dos veces
    # retomaba el estado de la corrida anterior en vez de empezar de nuevo — y
    # el diario existe justamente para que cada investigación sea una fila
    # aparte, no una continuación.
    from datetime import datetime
    config = {"configurable": {
        "thread_id": f"{inv.nombre}:{caso}:{datetime.now():%Y%m%d%H%M%S}"},
        "recursion_limit": 80}
    try:
        for evento in app.stream(estado, config, stream_mode="updates"):
            _mostrar(evento)
    except Exception as e:
        # Un traceback de Python no es una respuesta. Si igual se llega acá, se
        # dice qué pasó y con qué quedó — no se muere en silencio ni en jerga.
        print(f"\n⚠  La investigación se cortó: {type(e).__name__}: "
              f"{str(e).splitlines()[0][:200]}")
        v = app.get_state(config).values
        print(f"   Pensó {v.get('vueltas', 0)} vez/veces y usó "
              f"{len(v.get('intentos', []))} herramienta(s).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
