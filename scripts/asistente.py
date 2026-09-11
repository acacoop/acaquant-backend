"""`scripts/asistente.py` — HABLAR CON EL ASISTENTE DESDE LA TERMINAL.

    python -m scripts.asistente                        # conversación (se escribe y listo)
    python -m scripts.asistente "¿qué me vence?"       # una pregunta y chau
    python -m scripts.asistente --callado "…"          # sólo la respuesta, sin el detrás

── SU ROL EN EL CICLO: es la PANTALLA, y por ahora la única ──

No tiene nada de lógica: arma la pregunta, llama a `asistente.ciclo.preguntar()`
y dibuja los eventos que van saliendo. El día que esto sea un endpoint, lo único
que cambia es quién dibuja.

Y dibuja TODO a propósito. En cada vuelta se ve qué herramienta pidió el modelo,
con qué argumentos, y qué le devolvió el código. Sin eso, cuando conteste mal no
se puede saber si eligió mal la herramienta, si la herramienta trajo basura, o
si razonó mal con datos buenos — que son tres problemas distintos.

⚠️ ACÁ NO HAY CONTROL DE ACCESO, y no hace falta: esto corre en el Droplet, al
que sólo entra el admin. El día que sea un endpoint, ahí sí `require_admin`.

⚠️ GASTA PLATA DE VERDAD. Cada vuelta es una llamada a OpenAI, y queda anotada
en `ia.trazas` con este usuario.
"""
from __future__ import annotations

import argparse
import json
import sys

USUARIO_DEFAULT = "nicolas.mollo@acavalores.com.ar"

VERDE, ROJO, GRIS, AZUL, NEGRITA, FIN = (
    "\033[32m", "\033[31m", "\033[90m", "\033[36m", "\033[1m", "\033[0m")


def _dibujar(e: dict) -> None:
    """Un evento del ciclo → una línea en pantalla."""
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


def _pie(r: dict) -> None:
    """El costo de la pregunta, que conviene tener a la vista."""
    print(f"\n{GRIS}   {r['vueltas']} vuelta(s) · {r['tokens_in']:,} tokens de entrada · "
          f"{r['tokens_out']:,} de salida · trazas {r['trazas'] or '—'}{FIN}")
    if r["tokens_in"] > r["tokens_out"] * 5 and r["vueltas"] > 1:
        print(f"{GRIS}   (entra mucho más de lo que sale porque en CADA vuelta se le "
              f"reenvía todo:\n    el modelo no recuerda nada de la vuelta anterior){FIN}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pregunta", nargs="*", help="si no la pasás, entra en conversación")
    ap.add_argument("--usuario", default=USUARIO_DEFAULT,
                    help="con quién se anota el gasto en ia.trazas")
    ap.add_argument("--callado", action="store_true", help="sólo la respuesta")
    a = ap.parse_args()

    from asistente import ciclo

    ver = None if a.callado else _dibujar

    # ── Una sola pregunta ──
    if a.pregunta:
        r = ciclo.preguntar(" ".join(a.pregunta), usuario=a.usuario, ver=ver)
        if a.callado:
            print(r["respuesta"] or r["error"])
        elif r["error"]:
            print(f"\n{ROJO}{r['error']}{FIN}")
        if not a.callado:
            _pie(r)
        return 0 if r["respuesta"] else 1

    # ── Conversación. `historial` es lo que hace que la segunda pregunta
    # pueda decir «y de ese, ¿cuánto tengo?» sin repetir de qué habla. ──
    print(f"{NEGRITA}Asistente de la mesa{FIN} {GRIS}— Ctrl-C o 'chau' para salir.{FIN}")
    historial: list[dict] = []
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
        r = ciclo.preguntar(pregunta, usuario=a.usuario, historial=historial, ver=ver)
        if r["error"]:
            print(f"\n{ROJO}{r['error']}{FIN}")
        if a.callado and r["respuesta"]:
            print(r["respuesta"])
        if not a.callado:
            _pie(r)
        # ⚠️ El historial se guarda SIEMPRE, aunque la vuelta haya fallado: si
        # se descartara, la pregunta siguiente perdería el hilo justo después
        # de un error, que es cuando más se necesita poder repreguntar.
        historial = r["mensajes"]


if __name__ == "__main__":
    sys.exit(main())
