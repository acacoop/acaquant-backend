"""scripts/fix_churn_no_habil.py — deshace el churn que el finde estampó.

QUÉ PASÓ (2026-08-22, §0.cp): hasta el fix del calendario, el centinela corría
los detectores de mercado los días NO hábiles sobre la foto vieja del viernes.
Eso ESTAMPÓ en `agente.av_agent_items` cosas que no pasaron:

    estado='volvio' con vuelto_at un sábado   → «el arreglo falló» (mentira:
                                                nada corrió que pudiera fallar)

El gating impide que se repita, pero las marcas ya escritas quedan — y cada
`volvio` falso borra la confianza acumulada de un arreglo que sigue bien.

Este script:
  1. LISTA los items de dominio BONO cuyo `vuelto_at` cae en fecha ART no
     hábil (sábado, domingo o feriado) y siguen en estado `volvio`.
  2. Con `--aplicar`: los devuelve a `resuelto`, con `resuelto_at` = ese
     `vuelto_at` (lo más conservador: el reloj de hitos re-arranca ahí, en
     días hábiles vale 0 hasta el próximo hábil) y `vuelto_at` = NULL.

Solo dominio BONO a propósito: un `volvio` de SALUD en sábado es real (los
crons corren de noche). Read-only sin la bandera.

Correr en el Droplet:
    python -m scripts.fix_churn_no_habil            # medir
    python -m scripts.fix_churn_no_habil --aplicar  # deshacer
"""
from __future__ import annotations

import sys
from zoneinfo import ZoneInfo

from core import calendario
from core.postgres import get_pool

_ART = ZoneInfo("America/Argentina/Buenos_Aires")


def main() -> None:
    from api.services.av_agent import DOMINIO_EVAL
    aplicar = "--aplicar" in sys.argv
    tipos_bono = [t for t, d in DOMINIO_EVAL.items() if d == "bono"]
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT clave, sujeto, regla, vuelto_at FROM agente.av_agent_items "
            " WHERE estado = 'volvio' AND vuelto_at IS NOT NULL "
            "   AND tipo = ANY(%s)", (tipos_bono,))
        filas = [(c, s, r, v) for c, s, r, v in cur.fetchall()
                 if not calendario.es_habil(v.astimezone(_ART).date())]
        print(f"'volvio' estampados en día NO hábil (dominio bono): {len(filas)}")
        for _c, s, r, v in filas[:60]:
            print(f"  {s:<8} {r:<28} vuelto_at={v.astimezone(_ART):%d/%m %H:%M}")
        if not filas:
            print("nada que deshacer.")
            return
        if not aplicar:
            print("\nDRY-RUN: no se escribió nada. Para deshacer: --aplicar")
            return
        cur.execute(
            "UPDATE agente.av_agent_items "
            "   SET estado = 'resuelto', resuelto_at = vuelto_at, "
            "       vuelto_at = NULL "
            " WHERE clave = ANY(%s)", ([f[0] for f in filas],))
        conn.commit()
        print(f"deshechos: {cur.rowcount}")


if __name__ == "__main__":
    main()
