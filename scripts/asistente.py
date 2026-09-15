"""Hablar con el asistente desde la terminal. Corre en el Droplet, sin control
de acceso (solo entra el admin); gasta plata de verdad y queda en ia.llamadas.

    python -m scripts.asistente                        # conversación
    python -m scripts.asistente "¿qué me vence?"       # una pregunta
    python -m scripts.asistente --callado "…"          # sólo la respuesta
"""
from __future__ import annotations

import argparse
import json
import sys

USUARIO_DEFAULT = "nicolas.mollo@acavalores.com.ar"

VERDE, ROJO, GRIS, AZUL, NEGRITA, FIN = (
    "\033[32m", "\033[31m", "\033[90m", "\033[36m", "\033[1m", "\033[0m")


def _dibujar(e: dict) -> None:
    """Un evento del grafo → una línea en pantalla."""
    t = e["tipo"]
    if t == "pregunta":
        print(f"\n{NEGRITA}❓ {e['texto']}{FIN}")
        print(f"{GRIS}   herramientas que le ofrezco: {', '.join(e['herramientas'])}{FIN}")
    elif t == "vuelta":
        print(f"\n{GRIS}{'─' * 66}{FIN}")
        print(f"{AZUL}VUELTA {e['n']}{FIN} {GRIS}— le mando la conversación entera "
              f"y espero{FIN}")
    elif t == "pide":
        args = json.dumps(e["argumentos"], ensure_ascii=False) if e["argumentos"] else "{}"
        print(f"  {AZUL}🔧 PIDE{FIN}  {e['herramienta']}({args})")
        print(f"{GRIS}     (el modelo no la corre: sólo la pide por nombre){FIN}")
    elif t == "resultado":
        r = e["resultado"]
        if isinstance(r, dict) and r.get("error"):
            print(f"  {ROJO}✖ DEVUELVE{FIN}  {r['error']}")
            print(f"{GRIS}     (el error vuelve al modelo como dato, no corta nada){FIN}")
        else:
            resumen = json.dumps(r, ensure_ascii=False, default=str)
            print(f"  {VERDE}✔ EJECUTO{FIN}  {resumen[:220]}"
                  f"{'…' if len(resumen) > 220 else ''}")
    elif t == "texto":
        print(f"\n{GRIS}{'─' * 66}{FIN}\n{VERDE}💬{FIN} {e['texto']}")
    elif t == "corte":
        print(f"\n{ROJO}⛔ {e['motivo']}{FIN}")
    elif t == "despacho":
        print(f"\n{AZUL}🧭 DESPACHO{FIN} → {', '.join(e['agentes'])} {GRIS}({e['motivo']}){FIN}")
    elif t == "junta":
        print(f"\n{AZUL}🔗 JUNTA{FIN} de {', '.join(e['agentes'])}")


def _pie(r: dict) -> None:
    """El costo de la pregunta, que conviene tener a la vista."""
    print(f"\n{GRIS}   {r['vueltas']} vuelta(s) · {r['tokens_in']:,} tokens de entrada · "
          f"{r['tokens_out']:,} de salida · llamadas {r['llamadas'] or '—'}{FIN}")
    if r["tokens_in"] > r["tokens_out"] * 5 and r["vueltas"] > 1:
        print(f"{GRIS}   (entra mucho más de lo que sale porque en CADA vuelta se le "
              f"reenvía todo:\n    el modelo no recuerda nada de la vuelta anterior){FIN}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pregunta", nargs="*", help="si no la pasás, entra en conversación")
    ap.add_argument("--usuario", default=USUARIO_DEFAULT,
                    help="con quién se anota el gasto en ia.llamadas")
    ap.add_argument("--callado", action="store_true", help="sólo la respuesta")
    a = ap.parse_args()

    from asistente import permitido, sesiones

    # ⚠️ EL ALCANCE, ANTES DE PREGUNTAR NADA. Saber qué cuentas ve el asistente
    # no puede depender de acordarse de mirar el `.env`: si no se dice acá, una
    # respuesta parcial se lee como si fuera toda la casa.
    habilitadas = permitido.cuentas()
    if not a.callado:
        if habilitadas:
            print(f"{GRIS}cuentas habilitadas ({len(habilitadas)}): "
                  f"{', '.join(habilitadas)}{FIN}")
        else:
            print(f"{ROJO}⚠ No hay ninguna cuenta habilitada: el asistente no va a "
                  f"poder mirar datos.{FIN}\n"
                  f"{GRIS}  Se declaran en el `.env` del servidor:"
                  f"  {permitido.CLAVE_ENV}=id1,id2,id3{FIN}")

    def mostrar(r: dict) -> None:
        if not a.callado:
            for e in r["eventos"]:
                _dibujar(e)

    # ── Una sola pregunta ──
    if a.pregunta:
        r = sesiones.preguntar(" ".join(a.pregunta), usuario=a.usuario)
        mostrar(r)
        if a.callado:
            print(r["respuesta"] or r["error"])
        elif r["error"]:
            print(f"\n{ROJO}{r['error']}{FIN}")
        if not a.callado:
            _pie(r)
        return 0 if r["respuesta"] else 1

    # ── Conversación. La sesión guardada es lo que hace que la segunda
    # pregunta pueda decir «y de ese, ¿cuánto tengo?» sin repetir de qué habla. ──
    print(f"{NEGRITA}Asistente de la mesa{FIN} {GRIS}— Ctrl-C o 'chau' para salir.{FIN}")
    sesion: str | None = None
    while True:
        try:
            pregunta = input(f"\n{NEGRITA}vos>{FIN} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not pregunta:
            continue
        if pregunta.lower() in ("chau", "salir", "exit", "quit"):
            return 0
        r = sesiones.preguntar(pregunta, usuario=a.usuario, sesion=sesion)
        mostrar(r)
        sesion = r["sesion"]["id"]
        if r["error"]:
            print(f"\n{ROJO}{r['error']}{FIN}")
        if a.callado and r["respuesta"]:
            print(r["respuesta"])
        if not a.callado:
            _pie(r)


if __name__ == "__main__":
    sys.exit(main())
