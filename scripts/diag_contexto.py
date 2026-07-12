"""diag_contexto.py — imprime el CONTEXTO exacto de una vista del copiloto, sin LLM.

LA LUPA: para debuggear bloqueos de verificación (números "fantasma") y
bloques que no llegan (ej. [futuros DLR] ausente un fin de semana): muestra
qué bloques entraron, cuáles vinieron vacíos y qué números hay realmente.
Cero tokens — es el input del modelo, no la respuesta.

Uso (Droplet):
    python -m scripts.diag_contexto --vista home
    python -m scripts.diag_contexto --vista renta_fija --pregunta "GD30 vs AL30"
    python -m scripts.diag_contexto --vista renta_variable --filas 10

La vista `trading` necesita params del cliente (tarjetas) → acá sale con
tabla vacía; sus bloques generales (reloj, movers, CCL) se imprimen igual.
"""
from __future__ import annotations

import argparse

from api.services import copiloto


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vista", default="renta_fija", choices=sorted(copiloto.VISTAS))
    ap.add_argument("--pregunta", default="", help="para activar bloques por mención")
    ap.add_argument("--filas", type=int, default=5)
    args = ap.parse_args()

    cfg = copiloto.VISTAS[args.vista]
    filas = cfg["fetch"](None)
    enriquecer = cfg.get("enriquecer")
    if enriquecer:
        filas = enriquecer(filas)
    print(f"tabla: {len(filas)} filas · primeras {args.filas}:")
    print(copiloto._tsv(filas[: args.filas], cfg["columnas"]))

    extras = cfg.get("extras")
    if extras:
        print("\n── BLOQUES EXTRAS (esto es EXACTAMENTE lo que ve el modelo) ──")
        for linea in extras(filas, args.pregunta, [], None):
            print(linea)


if __name__ == "__main__":
    main()
