"""jobs/av_agent_sistema.py — LO QUE EL AGENTE SABE DEL SISTEMA, CADA 10 MINUTOS.

Doc madre: **`docs/AV_AGENT.md`** §0.dk.

POR QUÉ EXISTE
==============

El user (2026-08-24), mirando NOTICIAS DE LA BASE con cinco filas todas
fechadas «23/08 20:31» a las once de la mañana:

    *«lo de los jobs de noche no tiene sentido, esto necesito que sea
    prácticamente real time o cada 10 minutos, y que cada corrida no
    superponga cosas ya arregladas»*

Estos cuatro detectores vivían adentro de `jobs/db_tamano`, que corre **una vez
por día a las 23:30 UTC**, por una razón que ya no aplica: cuando nacieron, el
veredicto de frescura salía del perfil congelado por el barrido, así que
correrlos más seguido habría dado exactamente la misma respuesta. Eso se
arregló (`av_agent_contexto._ultimo_dato_vivo`: el ritmo se recuerda, el atraso
se mide al leer), y con eso ya no hay nada que los ate a la noche.

QUÉ SE QUEDÓ EN LA NOCHE Y POR QUÉ
==================================

    ACÁ (10 min)   tabla_quieta · db_cambio · dato_partido · cron_desalineado
                   Todo se lee de la base propia y de archivos. Barato.
    DE NOCHE       la FOTO de tamaños + el PERFIL de las ~200 tablas (una query
                   por tabla) + la SUPERFICIE HTTP, que hace ~400 requests
                   contra producción. Caro o intrusivo, y cambia despacio.

**Y por eso `superficie` es un alcance aparte** (`av_agent.ALCANCES_VIVOS`): los
dos son de REEMPLAZO, o sea que cada pasada pisa TODO lo de su alcance. Si este
job escribiera `sistema` con cuatro detectores y el nocturno también, el que
corriera último borraría los hallazgos del otro — cada diez minutos.

    python -m jobs.av_agent_sistema
"""
from __future__ import annotations

import logging
import sys

from core.job_runs import JobRunLogger

logger = logging.getLogger(__name__)


def _seguro(fn, nombre: str) -> list[dict] | None:
    """Corre un detector. **`None` = levantó**, que NO es lo mismo que «no
    encontró nada»: el que declara `evaluados` tiene que poder distinguirlos, o
    un detector caído cierra por ausencia todo lo suyo (§0.be)."""
    try:
        return list(fn() or [])
    except Exception as e:
        logger.warning("av_agent_sistema: el detector %s falló (%s)", nombre, e)
        return None


def detectores() -> tuple[list[dict], set[str]]:
    """Los cuatro baratos. Devuelve lo que vieron **y qué alcanzaron a mirar**.

    Se expone aparte del `main` para que el test pueda cruzar el catálogo de
    tipos sin tener que montar un `JobRunLogger`.
    """
    from api.services import av_agent
    from api.services import av_agent_contexto as ctx
    from api.services import av_agent_crontab as cron
    from api.services import av_agent_db as db

    sistema: list[dict] = []
    evaluados: set[str] = set()
    for tipos, fn, nombre in (
            # El RITMO lo recuerda el barrido nocturno; el ATRASO se mide acá,
            # en una sola query para todas las tablas con ritmo. Ésa es la
            # separación que permite que esto corra cada 10 minutos.
            (("tabla_quieta",), ctx.detectar_tablas, "tablas"),
            # Lee la foto de tamaños (que sigue siendo diaria) y la compara
            # contra la de ayer. Es barato y mantiene vivo el objeto: sin
            # confirmarlo, la pantalla no puede afirmar que sigue pasando.
            (("db_cambio",), db.detectar_db, "db"),
            # No depende del mercado ni de la hora: dos copias que difieren a
            # las 23:30 difieren igual a las 11.
            (("dato_partido",), av_agent.detectar_dato_partido, "dato_partido"),
            (("cron_desalineado",), cron.detectar_crontab, "crontab"),
    ):
        piezas = _seguro(fn, nombre)
        if piezas is None:      # el detector levantó: no se declara nada
            continue
        sistema += piezas
        evaluados.update(tipos)
    return sistema, evaluados


def main() -> int:
    with JobRunLogger("av_agent_sistema") as jr:
        from api.services import av_agent_registro as registro

        sistema, evaluados = detectores()
        r = registro.guardar("sistema", sistema, evaluados=evaluados)
        n = r.get("foto") or 0
        jr.set_stat("hallazgos", n)
        jr.set_stat("evaluados", len(evaluados))
        esp = r.get("espejo") or {}
        if esp.get("ok"):
            jr.set_stat("objetos_nuevos", esp.get("nuevos", 0))
            jr.set_stat("objetos_resueltos", esp.get("resueltos", 0))

        for h in sistema:
            print(f"  {h['severidad']:6} {h['tipo']:18} {h['ticker']:<38} "
                  f"{h['motivo'][:90]}")
        # **Decir SIEMPRE cuántos detectores corrieron**, no solo qué
        # encontraron: cero hallazgos con cuatro detectores caídos y cero
        # hallazgos con todo sano se leen igual, y son lo contrario.
        print(f"\n✔ {n} hallazgos · {len(evaluados)}/4 detectores declararon "
              f"haber corrido"
              + ("" if len(evaluados) == 4 else
                 "  ⚠ los que faltan NO cierran lo suyo por ausencia"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
