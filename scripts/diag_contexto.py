"""diag_contexto.py — imprime el CONTEXTO exacto de una vista del copiloto, sin LLM.

LA LUPA: para debuggear bloqueos de verificación (números "fantasma") y
bloques que no llegan (ej. [futuros DLR] ausente un fin de semana): muestra
qué bloques entraron, cuáles vinieron vacíos y qué números hay realmente.
Cero tokens de IA — es el input del modelo, no la respuesta.

LA BALANZA (--pesos, 2026-07-20): estima el COSTO en tokens de cada pieza del
prompt (reglas del system, tabla, y cada bloque extra por su etiqueta [x]) y
lo imprime ordenado de más pesado a más liviano, con el % del total. Es la
base para decidir recortes de contexto CON DATOS (técnica 1 del plan de
optimización, docs/COPILOTO.md). La estimación es chars/3.5 (aprox. castellano
+ números) — sirve para comparar pesos relativos, no para facturar.

Uso (Droplet):
    python -m scripts.diag_contexto --vista home
    python -m scripts.diag_contexto --vista renta_fija --pregunta "GD30 vs AL30"
    python -m scripts.diag_contexto --vista trading --pesos
    python -m scripts.diag_contexto --vista renta_variable --filas 10 --pesos

La vista `trading` necesita params del cliente (tarjetas) → acá sale con
tabla vacía; sus bloques generales (reloj, movers, CCL) se imprimen igual.
"""
from __future__ import annotations

import argparse
import sys

from api.services import copiloto

# Consolas Windows con codepage viejo (cp1252): un "→" en un print crashea el
# script entero. En el Droplet (UTF-8) no cambia nada.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass


def _tokens(texto: str) -> int:
    """Estimación barata: ~3.5 chars/token en castellano con números."""
    return round(len(texto) / 3.5)


def _etiqueta(linea: str) -> str:
    """Etiqueta del bloque: '[reloj de mercado] …' → '[reloj de mercado]'.
    Las líneas de continuación (sin etiqueta) heredan la del bloque anterior."""
    if linea.startswith("[") and "]" in linea:
        return linea[: linea.index("]") + 1]
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vista", default="renta_fija", choices=sorted(copiloto.VISTAS))
    ap.add_argument("--pregunta", default="", help="para activar bloques por mención")
    ap.add_argument("--filas", type=int, default=5)
    ap.add_argument("--pesos", action="store_true",
                    help="balanza: tokens estimados por pieza del prompt, orden desc")
    args = ap.parse_args()

    cfg = copiloto.VISTAS[args.vista]
    filas = cfg["fetch"](None)
    enriquecer = cfg.get("enriquecer")
    if enriquecer:
        filas = enriquecer(filas)
    tabla_completa = copiloto._tsv(filas, cfg["columnas"], cfg.get("celda_max", 60))
    print(f"tabla: {len(filas)} filas · primeras {args.filas}:")
    print(copiloto._tsv(filas[: args.filas], cfg["columnas"], cfg.get("celda_max", 60)))

    lineas_extras: list[str] = []
    extras = cfg.get("extras")
    if extras:
        print("\n── BLOQUES EXTRAS (esto es EXACTAMENTE lo que ve el modelo) ──")
        lineas_extras = list(extras(filas, args.pregunta, [], None))
        for linea in lineas_extras:
            print(linea)

    if not args.pesos:
        return

    # ── LA BALANZA ──────────────────────────────────────────────────────────
    piezas: list[tuple[str, int]] = [
        ("(system) reglas de la vista", _tokens(cfg.get("reglas") or "")),
        ("(system) base + tono (aprox, sin otras-vistas)",
         _tokens(copiloto.base._SYSTEM_BASE)),
        (f"tabla TSV ({len(filas)} filas x {len(cfg['columnas'])} col)",
         _tokens(tabla_completa)),
    ]
    # extras agrupados por etiqueta [x]; las continuaciones suman a su bloque
    grupos: dict[str, int] = {}
    actual = "(extras sueltos)"
    for linea in lineas_extras:
        et = _etiqueta(linea)
        if et:
            actual = et
        grupos[actual] = grupos.get(actual, 0) + len(linea)
    piezas += [(et, round(chars / 3.5)) for et, chars in grupos.items()]

    total = sum(t for _, t in piezas) or 1
    print(f"\n── LA BALANZA (tokens estimados, ~{total} total del prompt fijo) ──")
    print(f"{'pieza':58} {'tokens':>8} {'%':>6}")
    for nombre, toks in sorted(piezas, key=lambda p: -p[1]):
        print(f"{nombre[:58]:58} {toks:>8} {100 * toks / total:>5.1f}%")
    print("\n(estimación chars/3.5 — para comparar pesos relativos; el historial y "
          "la pregunta se suman aparte en cada llamada)")


if __name__ == "__main__":
    main()
