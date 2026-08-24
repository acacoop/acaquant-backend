"""jobs/seguimiento.py — de lo que se dio por arreglado, ¿qué aguantó y qué volvió?

Doc madre: **`docs/AV_AGENT.md`** — el mapa (`M.3`) y §0.ac/§0.de/§0.dh.
Corre una vez por día, después de que todos los detectores ya pasaron.

Es la pasada que convierte **el tiempo en evidencia**. Cada arreglo queda en
prueba y va cumpliendo hitos (1·2·3·7·14·30 días HÁBILES); si el problema no
vuelve, el arreglo era el bueno. Si vuelve, el arreglo no alcanzó — y eso es lo
que dice qué regla hay que reescribir.

⚠️⚠️ **ACÁ HABÍA DOS MEDIDORES CORRIENDO EN PARALELO, Y SE BORRÓ EL VIEJO**
(2026-08-24, Fase 2).

`av_agent_seguimiento` medía lo mismo por su cuenta, con su propia tabla y su
propia ventana de 5 días CORRIDOS. **Y no podía funcionar**: su veredicto salía
de `clave in claves_abiertas`, y las dos mitades armaban la clave distinto —

    se anotaba   `av_agent_acciones`  →  accion:objetivo:regla
    se comparaba `_claves_abiertas()` →  tipo:sujeto:regla

Los ids de acción están namespaceados (`mercado.*`, `assets.*`), así que la
intersección era **vacía por construcción**: `volvio` no podía pasar nunca y
todo lo que cumplía la ventana se sellaba `aguanto` → un ✔ `verificado` al eval
set, que la compuerta de autonomía cuenta igual que un click humano.

Queda UN medidor: `av_agent_items.cerrar_hitos()`, con la clave canónica
(`sujeto|causa`), el reloj en días HÁBILES (el finde no prueba nada) y la
distinción que el viejo no tenía — **cerrado por ACCIÓN vs cerrado por
AUSENCIA**, que es lo único que permite volver a votar sin fabricar señal.
"""
from __future__ import annotations

import logging
import sys

from core.job_runs import JobRunLogger

logger = logging.getLogger(__name__)


def main() -> int:
    with JobRunLogger("seguimiento") as jr:
        from api.services import av_agent_items

        h = av_agent_items.cerrar_hitos()
        if not h.get("ok"):
            print(f"✖ {h.get('error')}")
            jr.set_stat("error", h.get("error"))
            return 1

        jr.set_stat("mirados", h["mirados"])
        jr.set_stat("aguantaron", len(h["aguantaron"]))
        jr.set_stat("volvieron", len(h["volvieron"]))
        jr.set_stat("votos", h["votos"])

        print(f"\n{'=' * 72}\nHITOS — {h['mirados']} arreglo(s) con el reloj corriendo\n"
              f"{'=' * 72}")
        if h["volvieron"]:
            # Lo que volvió, PRIMERO: es lo único accionable de esta pasada.
            print(f"\n✖ {len(h['volvieron'])} VOLVIERON — el arreglo no era el bueno:")
            for x in h["volvieron"][:20]:
                print(f"   · {x['sujeto']:<14} {x['regla']}")
        if h["aguantaron"]:
            print(f"\n✔ {len(h['aguantaron'])} AGUANTARON todos los hitos:")
            for x in h["aguantaron"][:20]:
                print(f"   · {x['sujeto']:<14} {x['regla']}  ({x['dias']} días hábiles)")
        if not h["aguantaron"] and not h["volvieron"]:
            print("\n  Nada cerró hoy: los que están en prueba siguen en prueba.\n"
                  "  «Todavía no volvió» no es «aguantó».")

        en_prueba = h.get("en_prueba") or []
        if en_prueba:
            print(f"\n  {len(en_prueba)} en prueba. Los más avanzados:")
            for x in sorted(en_prueba, key=lambda x: -x["hitos"])[:8]:
                prox = x["proximo_hito_en_dias"]
                print(f"   · {x['sujeto']:<14} {x['hitos']}/{x['de']} hitos"
                      + (f" · próximo al día {prox}" if prox else ""))

        if h["votos"]:
            print(f"\n  → {h['votos']} voto(s) `verificado` al eval set. Es la "
                  f"evidencia más fuerte\n    que hay: no es la opinión de nadie, "
                  f"volvió o no volvió.")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
