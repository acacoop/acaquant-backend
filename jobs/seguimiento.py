"""jobs/seguimiento.py — de lo que se dio por arreglado, ¿qué aguantó y qué volvió?

Doc madre: **`docs/AV_AGENT.md`** §0.ac. Corre una vez por día, después de que
todos los detectores ya pasaron.

Es la pasada que convierte **el tiempo en evidencia**. Cada arreglo queda en
prueba unos días; si el problema no vuelve, el arreglo era el bueno y eso entra
al eval set como un ✔ `verificado` — la señal más fuerte que hay, porque no es la
opinión de nadie: volvió o no volvió, y el agente no controla eso.

Si vuelve, es un ✖ **con motivo de verdad**: no «me parece que está mal», sino
«se arregló el 17 y volvió a pasar». Eso es lo que dice qué regla hay que
reescribir.

⚠️ **Si no se puede leer qué sigue abierto, NO se toca ningún veredicto.** Dar
todo por bueno porque no pudimos mirar sería premiar el silencio, que es
exactamente lo contrario de lo que este job hace.
"""
from __future__ import annotations

import logging

from core.job_runs import JobRunLogger
from core.postgres import get_pool

logger = logging.getLogger(__name__)


def _claves_abiertas() -> set[str] | None:
    """Las identidades de los problemas que HOY siguen abiertos.

    Se juntan las DOS fuentes que tienen identidad estable: lo que vigila el
    centinela y lo que dejó la última corrida de los detectores. `None` si no se
    puede leer — y el que llama tiene que notar la diferencia.
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT clave FROM mercado.av_agent_centinela "
                        "WHERE resuelto_at IS NULL")
            claves = {r[0] for r in cur.fetchall() if r[0]}
            # Y los hallazgos vigentes, con la MISMA forma de clave que usa el
            # centinela (`tipo:sujeto:regla`): si cada uno armara la suya, un
            # arreglo se daría por bueno mirando la lista equivocada.
            cur.execute(
                "SELECT DISTINCT tipo || ':' || ticker || ':' || regla "
                "FROM mercado.av_agent_hallazgos")
            claves |= {r[0] for r in cur.fetchall() if r[0]}
        return claves
    except Exception as e:
        logger.warning("seguimiento: no pude leer qué sigue abierto (%s)", e)
        return None


def main() -> int:
    with JobRunLogger("seguimiento") as jr:
        from api.services import av_agent_seguimiento as seg

        abiertas = _claves_abiertas()
        r = seg.revisar(abiertas)
        if not r.get("ok"):
            print(f"✖ {r.get('error')}")
            jr.set_stat("error", r.get("error"))
            return 1

        aguantaron, volvieron = r["aguantaron"], r["volvieron"]
        jr.set_stat("mirados", r["mirados"])
        jr.set_stat("aguantaron", len(aguantaron))
        jr.set_stat("volvieron", len(volvieron))
        jr.set_stat("votos", r["votos"])

        print(f"\n{'=' * 72}\nSEGUIMIENTO — {r['mirados']} arreglo(s) en prueba\n"
              f"{'=' * 72}")
        if aguantaron:
            print(f"\n✔ {len(aguantaron)} AGUANTARON (el arreglo era el bueno):")
            for x in aguantaron[:20]:
                print(f"   · {x['sujeto']:<12} {x['regla']}")
        if volvieron:
            # Lo que volvió va SEGUNDO y con más detalle: es lo accionable.
            print(f"\n✖ {len(volvieron)} VOLVIERON (el arreglo no alcanzó, o la "
                  f"causa era otra):")
            for x in volvieron[:20]:
                print(f"   · {x['sujeto']:<12} {x['regla']}  "
                      f"— se arregló el {x['arreglado_at']:%d/%m}")
        if not aguantaron and not volvieron:
            print("\n  Nada cerró hoy: los que están en prueba siguen en prueba.\n"
                  "  «Todavía no volvió» no es «aguantó».")
        if r["votos"]:
            print(f"\n  → {r['votos']} voto(s) `verificado` al eval set. Es la "
                  f"evidencia más fuerte\n    que hay: no es la opinión de "
                  f"nadie, el problema volvió o no volvió.")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
