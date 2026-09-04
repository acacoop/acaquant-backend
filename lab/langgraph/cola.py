"""`lab/langgraph/cola.py` — LOS PEDIDOS DE INVESTIGACIÓN.

POR QUÉ HAY UNA COLA Y NO UNA LLAMADA DIRECTA
=============================================

Una investigación son 8 a 18 idas y vueltas al modelo: **entre uno y dos
minutos**. El proxy de Next corta a los 30 segundos (`maxDuration = 30`), y ese
corte se ve en pantalla **exactamente igual que un backend caído**.

Así que la pantalla no espera: pide, le dan un número, y pregunta cómo va. Es
el mismo patrón que ya usa el modal para todo lo demás.

QUÉ ES UNA FILA
===============

Un PEDIDO con su estado, no un resultado. El resultado vive en
`lab.investigaciones` (el diario) y acá queda apuntado con su id. Son dos cosas
distintas: el pedido se puede cancelar, fallar o repetirse; la investigación,
una vez hecha, es un hecho.

⚠️ **`pasos` SE ESCRIBE MIENTRAS CORRE**, no al final. Es lo que deja ver en la
pantalla qué herramienta está usando ahora mismo — que en la terminal era la
mitad del valor: sin los pasos, cuando contesta mal no se puede distinguir si
eligió mal la herramienta, si la herramienta trajo basura, o si razonó mal.

⚠️⚠️ **EL LAB NO CONOCE `core.postgres`, NI SIQUIERA PARA SU PROPIA COLA.**
Podría: es la conexión del sistema y puede todo. Justamente por eso no. Si
`lab/` importara la conexión que puede escribir en producción, la garantía
pasaría a depender de que nadie la use para otra cosa — y eso es una intención,
no una garantía. Un test lo prohíbe.
"""
from __future__ import annotations

import json
import logging

from lab.langgraph.base import escribir, leer

logger = logging.getLogger(__name__)

PENDIENTE, CORRIENDO, LISTO, ERROR = "pendiente", "corriendo", "listo", "error"

# El esquema de `lab.pedidos` vive en `sql/lab.sql`, junto con el del diario y
# los permisos de los dos roles. Ver el comentario de `diario.sql_esquema()`.


def encolar(tipo: str, caso: str, por: str = "") -> dict:
    """Pide una investigación. Devuelve el id para preguntar cómo va.

    **No arranca nada**: eso lo hace el daemon cuando la levanta. Por eso esto
    contesta en milisegundos aunque la investigación tarde dos minutos.
    """
    # Si ya hay una igual sin terminar, se devuelve ESA en vez de encolar otra:
    # dos investigaciones del mismo caso a la vez son el mismo trabajo hecho
    # dos veces, y encima pagado dos veces.
    r = leer("SELECT id FROM lab.pedidos WHERE tipo = %s AND upper(caso) = upper(%s) "
             "  AND estado = ANY(%s) ORDER BY at DESC LIMIT 1",
             (tipo, caso, [PENDIENTE, CORRIENDO]))
    if not isinstance(r, str) and r[1]:
        return {"ok": True, "id": r[1][0][0], "ya_estaba": True}
    try:
        f = escribir("INSERT INTO lab.pedidos (tipo, caso, por) "
                     "VALUES (%s,%s,%s) RETURNING id", (tipo, caso, por))
        return {"ok": True, "id": f[0] if f else None, "ya_estaba": False}
    except Exception as e:
        logger.warning("cola: no pude encolar (%s)", e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


def siguiente() -> dict | None:
    """El pedido más viejo que falta atender, marcado como CORRIENDO.

    ⚠️ El `UPDATE ... WHERE estado = 'pendiente' RETURNING` hace las dos cosas
    en UNA sentencia: si algún día hay dos procesos atendiendo, no pueden
    llevarse el mismo pedido. Un `SELECT` y después un `UPDATE` sí podrían.
    """
    try:
        f = escribir(
            "UPDATE lab.pedidos SET estado = %s, arrancado_at = now() "
            " WHERE id = (SELECT id FROM lab.pedidos WHERE estado = %s "
            "             ORDER BY at LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING id, tipo, caso, por", (CORRIENDO, PENDIENTE))
    except Exception as e:
        logger.warning("cola: no pude tomar el siguiente (%s)", e)
        return None
    if not f:
        return None
    return {"id": f[0], "tipo": f[1], "caso": f[2], "por": f[3]}


def anotar_paso(pedido_id: int, paso: dict) -> None:
    """Agrega un paso a la lista, mientras corre. Best-effort: perder un paso de
    la pantalla no puede tirar abajo la investigación que lo produjo."""
    try:
        escribir("UPDATE lab.pedidos SET pasos = pasos || %s::jsonb WHERE id = %s",
                 (json.dumps([paso], default=str), pedido_id))
    except Exception as e:
        logger.debug("cola: no pude anotar el paso (%s)", e)


def terminar(pedido_id: int, *, investigacion_id: int | None = None,
             error: str = "") -> None:
    try:
        escribir("UPDATE lab.pedidos SET estado = %s, terminado_at = now(), "
                 "  investigacion_id = %s, error = %s WHERE id = %s",
                 (ERROR if error else LISTO, investigacion_id, error[:500],
                  pedido_id))
    except Exception as e:
        logger.warning("cola: no pude cerrar el pedido %s (%s)", pedido_id, e)


def ver(pedido_id: int) -> dict | None:
    """Cómo va un pedido. Es lo que pollea la pantalla."""
    r = leer(
        "SELECT p.id, p.at, p.tipo, p.caso, p.por, p.estado, p.arrancado_at, "
        "       p.terminado_at, p.pasos, p.investigacion_id, p.error, "
        "       i.de_quien_es, i.que_paso, i.por_que, i.que_haria, "
        "       i.lo_que_no_se, i.de_donde "
        "  FROM lab.pedidos p "
        "  LEFT JOIN lab.investigaciones i ON i.id = p.investigacion_id "
        " WHERE p.id = %s", (pedido_id,))
    if isinstance(r, str) or not r[1]:
        return None
    cols, filas = r
    return dict(zip(cols, filas[0], strict=True))


def ultimos(limite: int = 20) -> dict:
    """La lista de la tab LAB: qué se pidió, cuándo y cómo terminó.

    ⚠️ **DEVUELVE SI PUDO MIRAR, NO SÓLO LAS FILAS.** La primera versión
    devolvía una lista y `[]` significaba las dos cosas: «no hay pedidos» y «no
    pude leer la base». Con la tabla sin crear, la terminal decía tranquila «No
    hay pedidos» — que es exactamente la confusión que este subsistema entero
    existe para atajar, cometida adentro de él.
    """
    r = leer("SELECT id, at, tipo, caso, por, estado, terminado_at, "
             "       investigacion_id, error "
             "  FROM lab.pedidos ORDER BY at DESC LIMIT %s", (int(limite),))
    if isinstance(r, str):
        return {"ok": False, "error": r, "pedidos": []}
    cols, filas = r
    return {"ok": True, "error": "",
            "pedidos": [dict(zip(cols, f, strict=True)) for f in filas]}


def investigables(limite: int = 80) -> dict:
    """**QUÉ SE PUEDE INVESTIGAR AHORA — leído de las MISMAS vistas que el modal.**

    ⚠️⚠️ **NO SE INVENTA UN CRITERIO PROPIO, Y ESA ES TODA LA REGLA.** La
    primera versión consultaba `agente.hallazgos` con su propio `WHERE` y
    ofrecía 60 casos mientras la pantalla mostraba 3 y 3: había TRES
    definiciones de «lo que está abierto» conviviendo sin árbitro (REGLA #9).

        AHORA      `v_ahora`     abierto + SIN LEER + detectado HOY (hora ARG)
        ENCONTRÓ   `v_encontro`  abierto + TIENE ARREGLO
        esta lista  ...           abierto, y nada más   ← el tercero, de más

    Ahora se leen **`agente.v_ahora` y `agente.v_encontro`**, que son las que
    dibujan las tabs. Si mañana cambia lo que la pantalla considera abierto,
    esta lista cambia con ella: no puede quedar desincronizada porque no tiene
    una copia del criterio.

    Las REINCIDENCIAS van aparte y primero: es la tabla que debería estar vacía.
    """
    from lab.langgraph.investigaciones import DE_LA_HABILIDAD

    habs = sorted(DE_LA_HABILIDAD)
    r = leer(
        # `DISTINCT ON` con el `orden` adelante: un hallazgo puede estar en las
        # dos vistas (abierto, con arreglo Y de hoy) y en el desplegable tiene
        # que aparecer UNA vez, con el origen más importante.
        "SELECT DISTINCT ON (habilidad, sujeto, regla) "
        "       origen, sujeto, habilidad, regla, cuando, que, orden "
        "  FROM ("
        "    SELECT 0 AS orden, 'reincidencia' AS origen, r.sujeto, r.habilidad, "
        "           r.regla, to_char(r.volvio_at,'YYYY-MM-DD HH24:MI') AS cuando, "
        "           'volvió después de «' || coalesce(r.arreglo_aplicado,'?') "
        "             || '», aguantó ' || round(r.dias_aguanto,1) || ' días' AS que "
        "      FROM agente.reincidencias r "
        "      JOIN agente.hallazgos h ON h.id = r.hallazgo_id "
        "     WHERE h.estado = ANY(%s) AND r.habilidad = ANY(%s) "
        "    UNION ALL "
        "    SELECT 1, 'encontro', e.sujeto, e.habilidad, e.regla, "
        "           to_char(e.detectado_at,'YYYY-MM-DD HH24:MI'), left(e.problema,160) "
        "      FROM agente.v_encontro e WHERE e.habilidad = ANY(%s) "
        "    UNION ALL "
        "    SELECT 2, 'ahora', a.sujeto, a.habilidad, a.regla, "
        "           to_char(a.detectado_at,'YYYY-MM-DD HH24:MI'), left(a.problema,160) "
        "      FROM agente.v_ahora a WHERE a.habilidad = ANY(%s) "
        "  ) t "
        " ORDER BY habilidad, sujeto, regla, orden "
        " LIMIT %s",
        (["nuevo", "en_curso", "reincidio"], habs, habs, habs, int(limite)))
    if isinstance(r, str):
        return {"ok": False, "error": r, "casos": []}
    cols, filas = r
    casos = []
    for f in filas:
        d = dict(zip(cols, f, strict=True))
        d["tipo"] = DE_LA_HABILIDAD.get(d["habilidad"], "")
        # Sin tipo no se puede investigar: no se ofrece. Mejor que no esté a
        # que esté y abra la investigación equivocada.
        if d["tipo"]:
            casos.append(d)
    # El orden final lo pone Python porque el `DISTINCT ON` obliga a ordenar
    # por la clave: reincidencias primero, después lo más reciente.
    casos.sort(key=lambda c: (c["orden"], c["cuando"]), reverse=False)
    casos.sort(key=lambda c: c["orden"])
    return {"ok": True, "error": "", "casos": casos}
