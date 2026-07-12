"""diag_contexto_rf.py — imprime el CONTEXTO exacto del copiloto RF, sin LLM.

Para debuggear bloqueos de verificación (números 'fantasma' tipo 156/143):
muestra qué bloques entraron, cuáles vinieron vacíos/fallados y qué números
hay realmente. Cero tokens — es el input, no la respuesta.

Uso (Droplet):
    python -m scripts.diag_contexto_rf                 # bloques + 5 filas de tabla
    python -m scripts.diag_contexto_rf --pregunta "GD30 vs AL30"   # con detección
"""
from __future__ import annotations

import argparse

from api.services import copiloto


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pregunta", default="", help="para activar bloques por mención")
    ap.add_argument("--filas", type=int, default=5)
    args = ap.parse_args()

    cfg = copiloto.VISTAS["renta_fija"]
    filas = cfg["fetch"](None)
    print(f"tabla: {len(filas)} bonos · primeras {args.filas} filas:")
    print(copiloto._tsv(filas[: args.filas], cfg["columnas"]))

    print("\n── BLOQUES EXTRAS (esto es EXACTAMENTE lo que ve el modelo) ──")
    for linea in cfg["extras"](filas, args.pregunta, [], None):
        print(linea)


if __name__ == "__main__":
    main()
