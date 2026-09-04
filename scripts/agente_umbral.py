"""`scripts/agente_umbral.py` — CAMBIAR UN UMBRAL DEL AGENTE, sin deploy.

Los umbrales de cada habilidad viven en el CÓDIGO (`agente/catalogo.py`) y la
BASE los pisa (`agente.habilidades.umbrales`, ver `catalogo.umbrales_de`). O sea
que ajustar cuán sensible es un detector **no es programar**: es cambiar un
número. Pero hasta hoy no había forma de hacerlo salvo SQL a mano, así que
«editable en caliente» era una frase y no una herramienta.

    python -m scripts.agente_umbral                              # todos
    python -m scripts.agente_umbral proveedor_caido              # los de una
    python -m scripts.agente_umbral proveedor_caido minimo_fallos 2
    python -m scripts.agente_umbral proveedor_caido minimo_fallos --default

PARA QUÉ SIRVE — el caso que lo motivó (2026-09-04)
===================================================

`proveedor_caido` salió con **41 episodios en 11 días** en el ranking de
crónicos, con mediana de 20 minutos. Y 20 minutos es EXACTAMENTE su
`ventana_s`: el detector borra el hallazgo cuando el último error tiene más de
eso, así que **un blip suelto vive el mínimo posible**. Una mediana pegada al
piso significa que la mayoría fueron blips.

La causa es `minimo_fallos: 1` — **un timeout suelto ya nace como hallazgo**.
Eso no se arregla relanzando nada ni investigando cuarenta veces lo mismo: se
arregla con este script.

LAS DOS GUARDAS
===============

  1. **La habilidad tiene que existir** en el catálogo.
  2. **La clave del umbral tiene que estar DECLARADA en el código.** Es la que
     importa: un `minimo_fallo` (sin la `s`) escribiría una clave que no lee
     nadie, el detector seguiría usando su default, y el que lo cambió se
     quedaría esperando un efecto que no va a llegar — sin error, sin log, sin
     nada. Es el modo de falla que este repo persigue, aplicado a su propia
     perilla.

Borrar la clave (`--default`) devuelve el valor del código. Nunca se pierde el
original: el código manda y la base sólo lo pisa.
"""
from __future__ import annotations

import argparse
import json
import sys

from agente import catalogo
from core.postgres import get_pool


def _en_base() -> dict[str, dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT nombre, umbrales FROM agente.habilidades")
        return {r[0]: dict(r[1] or {}) for r in cur.fetchall()}


def _mostrar(nombre: str | None) -> None:
    base = _en_base()
    for h in catalogo.HABILIDADES.values():
        if nombre and h.nombre != nombre:
            continue
        if not h.umbrales:
            if nombre:
                print(f"  «{h.nombre}» no declara ningún umbral.")
            continue
        print(f"\n  {h.nombre}   ({h.que_mira})")
        pisados = base.get(h.nombre) or {}
        for k, v in h.umbrales.items():
            if k in pisados and pisados[k] != v:
                print(f"    {k:<22} {pisados[k]!r:<12} ← EN BASE  (código: {v!r})")
            else:
                print(f"    {k:<22} {v!r:<12}   (del código)")
        # Una clave en la base que el código no declara NO la lee nadie.
        for k, v in pisados.items():
            if k not in h.umbrales:
                print(f"    ⚠ {k:<20} {v!r:<12} ← EN BASE y el código NO la declara: "
                      "no la lee nadie")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("habilidad", nargs="?", help="qué habilidad")
    ap.add_argument("clave", nargs="?", help="qué umbral")
    ap.add_argument("valor", nargs="?", help="el valor nuevo (número)")
    ap.add_argument("--default", action="store_true",
                    help="borra el valor de la base y vuelve al del código")
    a = ap.parse_args()

    if not a.habilidad or not a.clave:
        _mostrar(a.habilidad)
        print("\n  Para cambiar uno:")
        print("    python -m scripts.agente_umbral <habilidad> <clave> <valor>")
        print("    python -m scripts.agente_umbral <habilidad> <clave> --default")
        print("\n  El cambio lo toma la PRÓXIMA corrida de esa habilidad. No hace")
        print("  falta deploy ni reiniciar el daemon.")
        return 0

    h = catalogo.HABILIDADES.get(a.habilidad)
    if h is None:
        print(f"✗ «{a.habilidad}» no está en el catálogo.")
        return 1
    if a.clave not in h.umbrales:
        print(f"✗ «{a.habilidad}» no declara el umbral «{a.clave}».")
        print(f"  Declara: {', '.join(h.umbrales) or '(ninguno)'}")
        print("\n  Escribir una clave que el código no lee sería peor que no hacer")
        print("  nada: el detector seguiría con su default y vos esperando un")
        print("  efecto que no llega, sin error y sin log.")
        return 1
    if not a.default and a.valor is None:
        print(f"✗ falta el valor. Actual: {a.clave} = {h.umbrales[a.clave]!r}")
        return 1

    actuales = (_en_base().get(a.habilidad) or {})
    antes = actuales.get(a.clave, h.umbrales[a.clave])
    if a.default:
        actuales.pop(a.clave, None)
        despues = h.umbrales[a.clave]
    else:
        # El tipo lo manda el código: si el default es int, el nuevo es int.
        tipo = type(h.umbrales[a.clave])
        try:
            despues = tipo(a.valor)
        except (TypeError, ValueError):
            print(f"✗ «{a.valor}» no es un {tipo.__name__} — el umbral "
                  f"«{a.clave}» es {tipo.__name__} en el código.")
            return 1
        actuales[a.clave] = despues

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE agente.habilidades SET umbrales = %s WHERE nombre = %s",
                    (json.dumps(actuales), a.habilidad))
        if not cur.rowcount:
            print(f"✗ «{a.habilidad}» no está en la tabla. Corré el daemon una vez.")
            return 1

    print(f"\n  ✔ {a.habilidad}.{a.clave}:  {antes!r}  →  {despues!r}"
          + ("   (vuelve al valor del código)" if a.default else ""))
    print(f"\n  Lo toma la próxima corrida (cada {h.cada_segundos}s). Sin deploy.")
    print("  Para volver atrás: "
          f"python -m scripts.agente_umbral {a.habilidad} {a.clave} --default")
    return 0


if __name__ == "__main__":
    sys.exit(main())
