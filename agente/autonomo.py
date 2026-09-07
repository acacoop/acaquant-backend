"""`agente/autonomo.py` — EL EJECUTOR: lo que el agente aplica SOLO. Doc:
`AGENT.md` §0.ef.

Este módulo no arregla nada y no escribe nada: **ELIGE** qué hallazgos tocan
ahora y llama a `agente.arreglos.aplicar` con el actor del agente
(`tipos.ACTOR_AGENTE`) — la MISMA puerta que aprieta un humano, sobre el mismo
libro y el mismo estado. El juez sigue siendo el pre-flight que ya vive en
`arreglos.aplicar`: rechaza igual lo que `veredicto.puede_aplicar` no deja, y
acá no se toca nada de eso.

Tres guardas, y ninguna es opcional:

  · **tope por pasada** — un bug en el detector no puede escribir doscientos
    bonos en una corrida; cada alta gasta créditos de 1816 y segundos.
  · **no reintentar en 24 h** — si el agente ya lo intentó (bien o mal), no
    vuelve a insistir solo: el hallazgo queda como botón para una persona.
  · **solo reglas declaradas** — nada se aplica que la habilidad no haya
    marcado en `automatico` (`agente/catalogo.py`).
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
            reglas: set[tuple[str, str]], tope: int = TOPE_POR_PASADA) -> list[dict]:
    """PURA (testeable sin base).

    `abiertos`: filas con `id`, `habilidad`, `sujeto`, `regla`, `estado`,
    `arreglo`, `detectado_at`. `recientes`: tríos (habilidad, sujeto, regla)
    con acción del agente en la ventana de `NO_REINTENTAR_H`. `reglas`:
    (habilidad, regla) declaradas automáticas.
    """
    candidatas = [
        f for f in abiertos
        if (f["habilidad"], f["regla"]) in reglas
        and f["estado"] in (tipos.NUEVO, tipos.REINCIDIO)
        and (f.get("arreglo") or "")
        and (f["habilidad"], f["sujeto"], f["regla"]) not in recientes
    ]
    candidatas.sort(key=lambda f: f["detectado_at"])
    return candidatas[:tope]


def candidatos() -> list[dict]:
    """Lo que toca aplicar ahora, leído de la base. Vacío si no hay reglas
    declaradas."""
    from core.postgres import get_pool

    reglas = reglas_automaticas()
    if not reglas:
        return []
    habilidades = sorted({h for h, _ in reglas})
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, habilidad, sujeto, regla, estado, arreglo, detectado_at "
            "  FROM agente.hallazgos "
            " WHERE habilidad = ANY(%s) AND estado = ANY(%s) AND arreglo <> ''",
            (habilidades, [tipos.NUEVO, tipos.REINCIDIO]))
        cols = [c[0] for c in cur.description]
        filas = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

        cur.execute(
            "SELECT habilidad, sujeto, regla FROM agente.acciones "
            " WHERE por = %s AND at > now() - make_interval(hours => %s)",
            (tipos.ACTOR_AGENTE, NO_REINTENTAR_H))
        recientes = cur.fetchall()

    return _elegir(filas, {(r[0], r[1], r[2]) for r in recientes}, set(reglas))


def correr() -> dict:
    """Aplica los candidatos, uno por uno, y devuelve el resumen.

    **NUNCA levanta**: un arreglo que falla queda anotado en el libro (lo
    hace `arreglos.aplicar`) y no frena a los demás.
    """
    from agente import arreglos

    cands = candidatos()
    aplicados, fallidos = [], []
    for c in cands:
        try:
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
