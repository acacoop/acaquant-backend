"""api/services/av_agent_registro.py — **LA ÚNICA PUERTA POR LA QUE EL AGENTE
ESCRIBE LO QUE ENCONTRÓ.**

Doc madre: `docs/AV_AGENT.md` — el mapa (`M.3`, `M.8`) y §0.dj.

POR QUÉ EXISTE
==============

El user (2026-08-24), después de la cuarta semana con bugs nuevos:

    *«la arquitectura del agente está muy mal, hay bugs por todos lados,
    procesos independientes, no hay una lógica, cada día es peor»*

Y el número le daba la razón: **había SEIS puertas escribiendo estado**, cada
una con su propio criterio sobre qué guardar. No era un mal diseño — era una
migración a medias, con once comentarios «convive» sin terminar. El costo no se
paga en los bugs que ya salieron sino en los que faltan: **nada obligaba a una
funcionalidad nueva a usar el pipeline que ya existía**, así que cada feature
inauguraba su séptima puerta y el problema crecía solo.

Cerrar los bugs de a uno no arregla eso. Lo que lo arregla es que **no haya
dónde equivocarse**: una sola función escribe, y un test prohíbe el resto.

LAS DOS COSAS QUE SE ESCRIBEN, Y POR QUÉ SON DOS
================================================

    LA FOTO (`agente.av_agent_hallazgos`)   qué se ve AHORA, con su evidencia
                                            congelada. Es lo que dibuja LA LISTA.
    EL OBJETO (`agente.av_agent_items`)     quién es el problema y qué le pasó:
                                            desde cuándo, cuántas veces, si
                                            volvió, cómo se cerró.

Se separan porque contestan preguntas distintas y envejecen distinto: la foto se
reemplaza entera, el objeto **nunca pierde su historia**. Lo que no puede pasar
—y pasó— es que se escriba una y no la otra, o que se escriban con identidades
que no cruzan. Por eso van juntas, acá, en este orden.

LOS DOS MODOS, Y SE DERIVAN
===========================

    CORRIDA     la relevada nocturna: cada pasada deja su foto con `corrida_at`
                y la vigente es la última. Guarda historia; purga por TTL.
    REEMPLAZO   los monitores (`live`, `sistema`): cada pasada PISA lo suyo,
                porque contestan «¿qué está mal AHORA?» y acumular dejaría 84
                avisos del mismo problema al final del día.

**No es un parámetro**: sale de `alcance in av_agent.ALCANCES_VIVOS`, que ya era
la regla. Un parámetro sería una séptima forma de equivocarse.
"""
from __future__ import annotations

import json
import logging

from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Cuántas corridas se conservan. El valor de la tabla es la HISTORIA (¿esto
# apareció hoy o está hace un mes?), pero esa historia se lee en semanas, no en
# años: sin purga, una corrida diaria de ~50 hallazgos son ~18k filas/año que
# nadie consulta. Se purga en la MISMA transacción del insert (patrón de las
# fotos de Tesorería) para que no haga falta otro cron que se pueda olvidar.
TTL_CORRIDAS = 60


# ⚠️ Se llama `guardar` y no `registrar` a propósito: `av_agent_acciones.
# registrar` es EL LIBRO (qué tocó el agente y dónde), y dos funciones con el
# mismo nombre en el mismo subsistema se confunden en una revisión. Un test que
# nació de un HTTP 500 real las cruzaba por nombre y lo cantó al instante.
def guardar(alcance: str, hallazgos: list[dict], *,
              evaluados: set[str] | tuple[str, ...] = (),
              objetos: list[dict] | None = None) -> dict:
    """**Escribe la FOTO y el OBJETO.** La única puerta.

    `evaluados` son los tipos que esta pasada **alcanzó a mirar de verdad**, y
    es la guarda más importante del subsistema: solo esos se pueden cerrar por
    ausencia. Una corrida que mira menos de lo habitual —1816 no contesta, un
    detector levanta— no puede convertir su lista más corta en «se arreglaron 40
    problemas», que es la mentira más cara que puede decir una herramienta de
    integridad: deja el tablero en verde justo el día que está más ciega.

    ⚠️ **`objetos` casi nunca se pasa.** Por default, todo hallazgo es un objeto
    — que es la REGLA #10.1. La excepción son los avisos TRANSITORIOS que viajan
    en la foto y no son problemas: `recuperado` («el motor volvió») y `respuesta`
    («la pata que pediste ya cotiza») nacen y mueren en minutos, y darles ciclo
    de vida llenaría la memoria de objetos que se crean y se cierran cada cinco
    minutos sin que nadie los mire.

    Nunca levanta por el espejo: si `av_agent_items` falla, la foto —que es lo
    que se ve— ya está escrita y no se deshace por un problema de memoria.
    """
    from api.services import av_agent, av_agent_items

    alcance = (alcance or "").strip()
    if not alcance:
        return {"ok": False, "error": "sin alcance no se sabe qué se está "
                                      "reemplazando ni de qué corrida es"}
    hallazgos = list(hallazgos or [])
    de_reemplazo = alcance in av_agent.ALCANCES_VIVOS

    try:
        n = (_reemplazar(alcance, hallazgos) if de_reemplazo
             else _corrida(alcance, hallazgos))
    except Exception:
        logger.exception("av_agent_registro: no pude escribir la foto de %s",
                         alcance)
        raise

    # ── Y EL OBJETO ─────────────────────────────────────────────────────────
    #
    # ⚠️ **Va SIEMPRE, incluso con `hallazgos` vacío** — sobre todo con
    # `hallazgos` vacío. El día que una corrida no encuentra nada es el día en
    # que hay MÁS para cerrar, y `persistir()` cortaba antes de llegar acá.
    espejo: dict = {}
    try:
        espejo = av_agent_items.sincronizar(
            alcance, hallazgos if objetos is None else objetos,
            evaluados=evaluados)
    except Exception as e:
        logger.warning("av_agent_registro: no pude espejar %s (%s)", alcance, e)
        espejo = {"ok": False, "error": str(e)[:200]}

    return {"ok": True, "alcance": alcance, "modo": "reemplazo" if de_reemplazo
            else "corrida", "foto": n, "espejo": espejo}


def _fila(alcance: str, h: dict) -> tuple:
    """Una fila de la foto, **con su identidad ya calculada**.

    ⚠️ La `clave` la escribe el que produce el hallazgo, UNA sola vez, con
    `clave_de_problema`. Si el que lee la recalculara —en Python o en SQL—
    habría dos implementaciones de la misma identidad, que es exactamente cómo
    la memoria termina existiendo pero inalcanzable (REGLA #9). Ya pasó tres
    veces en este subsistema.
    """
    from api.services import av_agent_items

    return (alcance, h["tipo"], h["ticker"], h["regla"], h["severidad"],
            h["motivo"],
            json.dumps(h.get("evidencia") or {}, ensure_ascii=False,
                       default=str),
            av_agent_items.clave_de_problema(h["ticker"], h["regla"], alcance))


_INSERT = ("INSERT INTO agente.av_agent_hallazgos "
           "(alcance, tipo, ticker, regla, severidad, motivo, evidencia, clave) "
           "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)")


def _corrida(alcance: str, hallazgos: list[dict]) -> int:
    """Una CORRIDA: inserta y purga las viejas, **en una transacción**.

    Si el insert falla no queda una corrida a medias que parezca «hoy no
    encontró nada» — la peor mentira posible en una herramienta de integridad.
    """
    if not hallazgos:
        return 0
    filas = [_fila(alcance, h) for h in hallazgos]
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(_INSERT, filas)
        cur.execute(
            "DELETE FROM agente.av_agent_hallazgos WHERE corrida_at < ("
            "  SELECT min(c) FROM (SELECT DISTINCT corrida_at AS c "
            "    FROM agente.av_agent_hallazgos ORDER BY c DESC LIMIT %s) t)",
            (TTL_CORRIDAS,))
    return len(filas)


def _reemplazar(alcance: str, hallazgos: list[dict]) -> int:
    """Un REEMPLAZO: pisa todo lo del alcance, **en una transacción**.

    El DELETE y el INSERT van juntos a propósito: en dos pasos, entre uno y otro
    la pantalla mostraría cero hallazgos y alguien podría leer «está todo bien»
    justo cuando no lo está.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM agente.av_agent_hallazgos WHERE alcance = %s",
                    (alcance,))
        for h in hallazgos:
            cur.execute(_INSERT, _fila(alcance, h))
        conn.commit()
    return len(hallazgos)
