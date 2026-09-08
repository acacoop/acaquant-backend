"""`agente/autonomo.py` — EL EJECUTOR: lo que el agente aplica SOLO. Doc:
`AGENT.md` §0.ef.

Este módulo no arregla nada y no escribe nada: **ELIGE** qué hallazgos tocan
ahora y llama a `agente.arreglos.aplicar` con el actor del agente
(`tipos.ACTOR_AGENTE`) — la MISMA puerta que aprieta un humano, sobre el mismo
libro y el mismo estado. El juez sigue siendo el pre-flight que ya vive en
`arreglos.aplicar`: rechaza igual lo que `veredicto.puede_aplicar` no deja, y
acá no se toca nada de eso.

Cuatro guardas, y ninguna es opcional:

  · **tope por pasada** — un bug en el detector no puede escribir doscientos
    bonos en una corrida; cada alta gasta créditos de 1816 y segundos.
  · **no reintentar en 24 h** — si el agente ya lo intentó (bien o mal), no
    vuelve a insistir solo: el hallazgo queda como botón para una persona.
    Excepto lo REPETIBLE (`arreglos.Arreglo.repetible`): ahí un intento que
    salió bien no cierra la puerta, porque el sujeto es una FAMILIA y puede
    seguir habiendo cosas nuevas para completar — solo un intento FALLIDO
    frena la reinsistencia.
  · **solo reglas declaradas** — nada se aplica que la habilidad no haya
    marcado en `automatico` (`agente/catalogo.py`).
  · **lo que pide datos solo va si el arreglo sabe decir qué escribiría**
    (`Arreglo.solo`), y eso es únicamente lo determinístico: lo del modelo
    nunca se aplica sin que alguien apriete.
"""
from __future__ import annotations

import logging

from agente import tipos

logger = logging.getLogger(__name__)

TOPE_POR_PASADA = 5      # altas que gastan créditos de 1816 y segundos; y un bug no puede escribir 200 bonos en una pasada
NO_REINTENTAR_H = 24     # si lo intentó (bien o mal) en estas horas, no vuelve solo: queda como botón


def reglas_automaticas() -> dict[tuple[str, str], str]:
    """{(habilidad, regla): motivo} de todo lo declarado en el catálogo."""
    from agente import catalogo
    return {(h.nombre, r): m for h in catalogo.HABILIDADES.values() for r, m in h.automatico.items()}


def _elegir(abiertos: list[dict], recientes: set[tuple[str, str, str]],
            reglas: set[tuple[str, str]], tope: int = TOPE_POR_PASADA,
            repetibles: frozenset[tuple[str, str]] = frozenset(),
            recientes_ok: set[tuple[str, str, str]] = frozenset()) -> list[dict]:
    """PURA (testeable sin base).

    `abiertos`: filas con `id`, `habilidad`, `sujeto`, `regla`, `estado`,
    `arreglo`, `detectado_at`. `reglas`: (habilidad, regla) declaradas
    automáticas. `repetibles`: (habilidad, regla) cuyo arreglo es de FAMILIA
    (`Arreglo.repetible`) — ahí `en_curso` sigue siendo candidato, porque el
    sujeto es un CAMPO entero y aplicar de nuevo no duplica nada.

    `recientes` y `recientes_ok` son los dos lados de «el agente ya lo
    intentó en `NO_REINTENTAR_H`»: `recientes` (fallidos) frena a TODOS por
    igual, `recientes_ok` (una acción que salió bien) solo frena a lo NO
    repetible — para una familia, un intento exitoso no cierra la puerta:
    puede seguir habiendo títulos nuevos que completar.
    """
    candidatas = []
    for f in abiertos:
        clave = (f["habilidad"], f["regla"])
        if clave not in reglas or not (f.get("arreglo") or ""):
            continue
        es_repetible = clave in repetibles
        estados = (tipos.NUEVO, tipos.REINCIDIO)
        if es_repetible:
            estados += (tipos.EN_CURSO,)
        if f["estado"] not in estados:
            continue
        trio = (f["habilidad"], f["sujeto"], f["regla"])
        if trio in recientes:
            continue
        if trio in recientes_ok and not es_repetible:
            continue
        candidatas.append(f)
    candidatas.sort(key=lambda f: f["detectado_at"])
    return candidatas[:tope]


def candidatos() -> list[dict]:
    """Lo que toca aplicar ahora, leído de la base. Vacío si no hay reglas
    declaradas."""
    from agente import arreglos, catalogo
    from core.postgres import get_pool

    reglas = reglas_automaticas()
    if not reglas:
        return []
    habilidades = sorted({h for h, _ in reglas})
    # Qué (habilidad, regla) son REPETIBLES: sale del arreglo declarado, no
    # de una lista aparte — el sujeto es un CAMPO (una familia) y volver a
    # aplicarlo no duplica nada.
    repetibles = {
        (h.nombre, r) for h in catalogo.HABILIDADES.values() for r in h.automatico
        if (aid := h.arreglos.get(r)) and arreglos.ARREGLOS.get(aid)
        and arreglos.ARREGLOS[aid].repetible
    }
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, habilidad, sujeto, regla, estado, arreglo, detectado_at, "
            "       evidencia "
            "  FROM agente.hallazgos "
            " WHERE habilidad = ANY(%s) AND estado = ANY(%s) AND arreglo <> ''",
            (habilidades, [tipos.NUEVO, tipos.REINCIDIO, tipos.EN_CURSO]))
        cols = [c[0] for c in cur.description]
        filas = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

        cur.execute(
            "SELECT habilidad, sujeto, regla, ok FROM agente.acciones "
            " WHERE por = %s AND at > now() - make_interval(hours => %s)",
            (tipos.ACTOR_AGENTE, NO_REINTENTAR_H))
        acciones = cur.fetchall()

    recientes = {(h, s, r) for h, s, r, ok in acciones if not ok}
    recientes_ok = {(h, s, r) for h, s, r, ok in acciones if ok}
    return _elegir(filas, recientes, set(reglas), repetibles=repetibles,
                   recientes_ok=recientes_ok)


def correr() -> dict:
    """Aplica los candidatos, uno por uno, y devuelve el resumen.

    **NUNCA levanta**: un arreglo que falla queda anotado en el libro (lo
    hace `arreglos.aplicar`) y no frena a los demás.
    """
    from agente import arreglos

    cands = candidatos()
    aplicados, fallidos = [], []
    for c in cands:
        a = arreglos.ARREGLOS.get(c["arreglo"])
        try:
            if a is not None and a.pide_datos:
                # Lo que este arreglo escribiría SOLO, sin persona — o `None`
                # si no sabe. Solo lo determinístico: lo del modelo nunca
                # entra por acá.
                datos = a.solo(c["sujeto"], c.get("evidencia") or {})
                if not datos:
                    logger.debug(
                        "agente: %s/%s sobre %s no tiene nada determinístico "
                        "para escribir solo — no hubo intento",
                        c["habilidad"], c["regla"], c["sujeto"])
                    continue
                r = arreglos.aplicar(c["id"], por=tipos.ACTOR_AGENTE, datos=datos)
            else:
                r = arreglos.aplicar(c["id"], por=tipos.ACTOR_AGENTE)
        except Exception as e:
            r = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
        if r.get("ok"):
            aplicados.append(c["sujeto"])
            logger.info("agente: apliqué SOLO %s/%s sobre %s",
                        c["habilidad"], c["regla"], c["sujeto"])
        else:
            fallidos.append({"sujeto": c["sujeto"],
                             "error": str(r.get("error") or "")[:200]})
            logger.warning("agente: intenté SOLO %s/%s sobre %s y no cerró: %s",
                           c["habilidad"], c["regla"], c["sujeto"], r.get("error"))
    return {"candidatos": len(cands), "aplicados": aplicados, "fallidos": fallidos}
