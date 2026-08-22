"""scripts/fix_evals_comunicaciones.py — limpia votos `verificado` contaminados.

QUÉ MIDE Y QUÉ ARREGLA
======================

Hasta el fix de `core/ciclo.TIPOS_COMUNICACION` (2026-08-22), `cerrar_hitos`
miraba TODOS los items resueltos/vueltos — incluidos los avisos y preguntas,
que no son arreglos de nada. Cada uno de esos podía dejar en
`mercado.av_agent_evals` un voto con `origen='verificado'` (los que la
compuerta de autonomía cuenta a la par de un voto humano) y
`ref = 'aguanto:<clave>'` o `'volvio:<clave>'`.

Este script:
  1. CUENTA cuántos votos `verificado` apuntan a un item cuyo `tipo` es una
     comunicación (`aviso`, `aviso_fila`, `pregunta`) — el JOIN va por la
     `clave` que el propio `ref` lleva adentro.
  2. Con `--aplicar`, los BORRA. Sin la bandera es 100% read-only.

Correr en el Droplet:
    python -m scripts.fix_evals_comunicaciones            # medir (no escribe)
    python -m scripts.fix_evals_comunicaciones --aplicar  # borrar
"""
from __future__ import annotations

import sys

from core import ciclo
from core.postgres import get_pool

_SQL_LISTAR = """
SELECT e.id, e.caso, e.causa, e.origen, e.ref, i.tipo
  FROM mercado.av_agent_evals e
  JOIN mercado.av_agent_items i
    ON e.ref IN ('aguanto:' || i.clave, 'volvio:' || i.clave)
 WHERE e.origen = 'verificado'
   AND i.tipo = ANY(%s)
"""


def main() -> None:
    aplicar = "--aplicar" in sys.argv
    tipos = list(ciclo.TIPOS_COMUNICACION)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(_SQL_LISTAR, (tipos,))
        filas = cur.fetchall()
        print(f"votos 'verificado' que apuntan a comunicaciones: {len(filas)}")
        for id_, caso, causa, _origen, ref, tipo in filas[:50]:
            print(f"  #{id_}  caso={caso!r} causa={causa!r} tipo={tipo} ref={ref}")
        if len(filas) > 50:
            print(f"  … y {len(filas) - 50} más")
        if not filas:
            print("nada que limpiar.")
            return
        if not aplicar:
            print("\nDRY-RUN: no se borró nada. Para borrar: --aplicar")
            return
        cur.execute("DELETE FROM mercado.av_agent_evals WHERE id = ANY(%s)",
                    ([f[0] for f in filas],))
        conn.commit()
        print(f"borrados: {cur.rowcount}")


if __name__ == "__main__":
    main()
