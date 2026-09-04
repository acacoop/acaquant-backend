"""`agente/triage.py` — **A QUÉ VALE LA PENA IR A INVESTIGAR.** Doc: `AGENT.md` §6.9.

Este módulo NO investiga y no conoce al investigador: **elige**. Devuelve qué
hallazgos merecen que alguien vaya a averiguar por qué pasaron, y `jobs/agente.py`
—que es el único que conoce las dos mitades— los encola. Así el agente sigue
funcionando igual si el laboratorio no está instalado, que es la garantía que no
se negocia.

LA PALABRA
==========

*Triage* es lo de la guardia de un hospital: cuando entran diez juntos y no se
puede atender a todos a la vez, **alguien decide a quién primero**. No es curar,
es elegir. Acá hace falta porque investigar CUESTA —entre 8 y 18 llamadas al
modelo, uno o dos minutos, y plata— y hay ~40 hallazgos abiertos en cualquier
momento. Investigarlos todos sería pagar cuarenta veces para encontrar dos.

LA REGLA QUE LO HACE ÚTIL, Y LA DIJO EL USER
============================================

> *«el agente tranquilamente puede ver 5 minutos después si eso ya funciona y
> listo — esto tiene que ser cuando se termina de caer del todo»*

**No se investiga lo que se acaba de caer: se investiga lo que SIGUE caído.**

`proveedor_caido` corre cada 5 minutos y le alcanza UN fallo para cantar. Aunesa
se cayó a las 12:35, el hallazgo nació a las 12:41, y para cuando lo fuimos a
mirar ya no estaba: se había recuperado solo. Disparar en el momento del
hallazgo habría pagado una investigación entera de algo que se arregló sin que
nadie hiciera nada.

Por eso cada regla declara **cuánto tiene que aguantar** el problema antes de
merecer una investigación. Y no hace falta un reloj nuevo para medirlo: el
detector ya vuelve a mirar solo, y si el problema se fue, el hallazgo se cierra.
**Lo que sobrevive es lo que es real.**

⚠️ **EL TIPO DE INVESTIGACIÓN NO SE DECLARA ACÁ.** Ya vive en el laboratorio
(`lab.langgraph.investigaciones.DE_LA_HABILIDAD`), que es quien sabe qué sabe
investigar. Repetirlo en el catálogo serían dos mapas del mismo hecho sin
árbitro (REGLA #9), y el que se desincronice no falla: manda a investigar con el
método equivocado. Acá se declara **QUÉ reglas** y **CUÁNTO tienen que aguantar**
— nada más.
"""
from __future__ import annotations

import logging

from agente import catalogo, tipos
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Cuántas investigaciones automáticas por día. **Es un techo de PLATA**, y va
# declarado y no en un `while`: el día que una habilidad empiece a cantar 30
# sujetos, el tope es lo que separa «se investigaron los tres que importaban» de
# una factura. Al llegar, se corta y se dice — un tope silencioso es un tope que
# nadie sabe que existe.
TOPE_DIARIO = 6

# Cuántas horas tienen que pasar para volver a investigar el MISMO caso. El
# problema puede seguir abierto una semana; la respuesta a «por qué pasó» no
# cambia todos los días.
NO_REPETIR_H = 24


def _declaradas() -> list[tuple[str, str, int]]:
    """`(habilidad, regla, cuánto tiene que aguantar)` de todo lo declarado."""
    return [(h.nombre, regla, int(seg))
            for h in catalogo.HABILIDADES.values()
            for regla, seg in (h.investigar or {}).items()]


def candidatos(limite: int = 5) -> list[dict]:
    """Los hallazgos que **siguen abiertos** después de su espera declarada.

    No decide si se van a investigar de verdad: eso depende del tope diario y de
    si ya se investigó ese caso hace poco, y las dos cosas las sabe la cola. Acá
    sólo salen los que califican por su cuenta.

    Ordenados por antigüedad: el que aguantó más es el que menos probable es que
    se arregle solo.
    """
    declaradas = _declaradas()
    if not declaradas:
        return []
    habs = [d[0] for d in declaradas]
    reglas = [d[1] for d in declaradas]
    esperas = [d[2] for d in declaradas]
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            # ⚠️ Las tres listas van EN PARALELO por `unnest`, no pegadas en un
            # string. Es la misma razón que en `registro._cerrar_ausentes`:
            # cualquier separador es una apuesta a que no aparezca en los datos,
            # y un sujeto acá puede ser `mercado.market_snapshot` o `/api/x/{id}`.
            cur.execute(
                "SELECT h.id, h.habilidad, h.sujeto, h.regla, h.severidad, "
                "       round(extract(epoch FROM now() - h.detectado_at)/60) AS min_abierto "
                "  FROM agente.hallazgos h "
                "  JOIN unnest(%s::text[], %s::text[], %s::int[]) "
                "         AS d(hab, regla, aguanta) "
                "    ON d.hab = h.habilidad AND d.regla = h.regla "
                " WHERE h.estado = ANY(%s) "
                "   AND h.detectado_at <= now() - make_interval(secs => d.aguanta) "
                " ORDER BY h.detectado_at LIMIT %s",
                (habs, reglas, esperas, list(tipos.ABIERTOS), int(limite)))
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, f, strict=True)) for f in cur.fetchall()]
    except Exception as e:
        # ⚠️ **NO PODER ELEGIR NO ES «NO HAY NADA QUE INVESTIGAR»**, pero acá las
        # dos cosas se atienden igual —no se investiga— y eso está bien: el
        # costo de no investigar es no enterarse de algo antes; el de investigar
        # a ciegas es gastar sin saber en qué. Se logea para que no sea mudo.
        logger.warning("triage: no pude elegir (%s) — esta pasada no investiga", e)
        return []
