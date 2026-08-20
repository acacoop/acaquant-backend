"""api/services/av_agent_respuesta.py — LA RESPUESTA QUE LLEGA DESPUÉS.

Doc madre: **`docs/AV_AGENT.md`** §0.ak.

⚠️ **No confundir con `av_agent_seguimiento`**, que es otra cosa y mide otro
tiempo: aquél pregunta *«el arreglo que hicimos, ¿aguantó cinco días?»* (§0.ac).
Éste pregunta *«lo que le pedimos al mercado, ¿contestó?»*, y se responde adentro
de la misma rueda.

EL BUG QUE LO ORIGINA
=====================

El user, mirando la pantalla (2026-08-20):

    BUSCAR LA PATA USD ✔ pedida · pedida; todavía sin precio — si pasa una
                        rueda entera y no llega, ESA pata no cotiza

    *«eso es SUPER INMEDIATO… no es ni 1 seg, ya sabe si da o no da punta.
    Me resulta raro. Acá le falta un pasito más: está bien, sí, pero no
    terminás de entender ni te quedás tranquilo.»*

Tenía razón, y el problema no era el texto. **`verificar()` corría cero segundos
después de `aplicar()`**, y el `adhoc_watcher` del motor levanta la suscripción
recién a los 5 s — el precio, si llega, llega después. O sea que esa frase era la
ÚNICA que la función podía devolver, siempre, para todos los casos.

> **Un chequeo que solo tiene una respuesta posible no es un chequeo: es un
> cartel.** Y engaña más que no verificar, porque parece que verificó.

LA SEPARACIÓN QUE FALTABA
=========================

Hay DOS preguntas y se estaban contestando como una sola:

    ¿la acción hizo lo suyo?      ¿quedó pedida?        → se sabe AL INSTANTE
    ¿y la pregunta que abría?     ¿esa pata cotiza?     → la contesta el MERCADO

La primera la controla el agente y se verifica al aplicar. La segunda **necesita
tiempo de rueda**, y por eso la propuesta queda en estado `esperando` hasta que
haya con qué contestarla.

CÓMO CIERRA
===========

Corre en el monitor de rueda (cada 5'), relee cada propuesta que quedó esperando
y la cierra cuando puede:

    llegó precio            → la pata SÍ cotiza · y hay un paso siguiente real
    se agotó la espera      → la pata NO cotiza · confirmado, deja de ser un tema

**Las dos son noticias y las dos se cantan.** La segunda especialmente: es la que
le saca el tema de encima al que mira la pantalla, y es exactamente lo que el
user pedía con *«no te quedás tranquilo»*. Un "no" medido cierra un tema; un
silencio lo deja abierto para siempre.

⚠️ **La espera se mide en segundos de MERCADO ABIERTO**, no de reloj — la misma
regla de §0.u. Una pata pedida a las 16:50 no estuvo "3 horas sin punta" a las
20:00: estuvo 40 minutos y después cerró el mercado.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# El estado que dice «se aplicó, y la respuesta todavía no se puede saber». No
# es `aplicada` (nadie contestó la pregunta) ni `fallida` (la acción anduvo).
ESPERANDO = "esperando"

# Cuántas propuestas se revisan por pasada. Tope de cortesía: cada una cuesta un
# par de queries y el monitor corre cada 5 minutos.
TOPE = 40


def revisar(*, ahora: datetime | None = None) -> list[dict]:
    """Cierra lo que ya se puede cerrar. Devuelve los hallazgos a publicar.

    **Nunca levanta**: corre adentro del monitor de rueda y una excepción acá
    apagaría los detectores del mismo ciclo.
    """
    try:
        return _revisar(ahora or datetime.now(UTC))
    except Exception as e:
        logger.warning("av_agent_respuesta: no pude revisar (%s)", e)
        return []


def _revisar(ahora: datetime) -> list[dict]:
    filas = pendientes()
    if not filas:
        return []

    from api.services.av_agent_hacer import ACCIONES, Propuesta

    out: list[dict] = []
    for f in filas:
        a = ACCIONES.get(f["accion"])
        espera = getattr(a, "espera_s", 0) if a is not None else 0
        if a is None or not espera:
            # La acción dejó de existir o dejó de esperar respuesta: cerrarla es
            # mejor que dejarla aguardando a alguien que ya no va a venir.
            _cerrar(f["id"], True, "la acción ya no espera respuesta")
            continue

        p = Propuesta(sujeto=f["sujeto"], campo=f["campo"],
                      propuesto=f["propuesto"], porque=f["porque"] or "",
                      antes=f["antes"] or "", fuente=f["fuente"],
                      extra=f["extra"] or {})
        try:
            v, detalle = a.veredicto(p)
        except Exception as e:
            logger.warning("av_agent_respuesta: %s no pudo opinar de %s (%s)",
                           f["accion"], f["sujeto"], e)
            continue

        if v is True:
            _cerrar(f["id"], True, detalle)
            out.append(_hallazgo(f, "cotiza", detalle, ahora))
            continue

        esperado = _de_rueda(f["aplicado_at"], ahora)
        if v is None and esperado < espera:
            continue                       # todavía no hay con qué contestar

        # ⚠️ Se agotó la espera **y eso es una respuesta**, no un timeout. La
        # acción anduvo (quedó pedida); lo que se confirmó es que del otro lado
        # no hay nada. Por eso se cierra en `True`: conseguir el «no» era el punto.
        cerrado = detalle if v is False else (
            f"escuchamos {_cuanto(esperado)} de rueda y nadie puso punta")
        _cerrar(f["id"], True, cerrado)
        out.append(_hallazgo(f, "no_cotiza", cerrado, ahora))
    return out


def pendientes() -> list[dict]:
    """Las propuestas aplicadas cuya respuesta todavía no llegó."""
    from core.postgres import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, accion, sujeto, campo, antes, propuesto, porque, fuente, "
            "       extra, aplicado_at, por "
            "  FROM mercado.av_agent_propuestas "
            " WHERE estado = %s AND aplicado_at IS NOT NULL "
            " ORDER BY aplicado_at LIMIT %s", (ESPERANDO, TOPE))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _cerrar(pid: int, ok: bool, detalle: str) -> None:
    from core.postgres import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE mercado.av_agent_propuestas "
            "   SET estado = %s, verificado = %s, verificado_detalle = %s "
            " WHERE id = %s",
            ("aplicada" if ok else "fallida", ok, (detalle or "")[:500], pid))


def _hallazgo(f: dict, cual: str, detalle: str, ahora: datetime) -> dict:
    """La noticia, con la MISMA forma que el resto — la pantalla no aprende nada
    nuevo. Vence rápido (`VENCE_RAPIDO`): es una novedad, no un problema."""
    from api.services.av_agent import _hhmm

    sujeto = f["sujeto"]
    if cual == "cotiza":
        motivo = f"{sujeto}: la pata en dólares SÍ cotiza · {detalle} · {_hhmm(ahora)}"
        texto = ("Se pidió y llegó precio. Ahora sí se puede decidir apuntar el "
                 "master a esa pata — eso **requiere reiniciar `motor_rofex` "
                 "fuera de rueda**, así que no se hace solo.")
    else:
        motivo = f"{sujeto}: la pata en dólares NO cotiza · {detalle} · {_hhmm(ahora)}"
        texto = ("Se pidió, la escuchamos y el mercado nunca puso punta. El bono "
                 "cotiza en pesos porque su pata en dólares no opera: no es un "
                 "dato mal cargado y no hay nada que hacer. La valuación estaba "
                 "bien todo el tiempo (el motor divide por el MEP).")
    return {"tipo": "respuesta", "ticker": sujeto, "regla": cual,
            "severidad": "baja", "motivo": motivo[:200],
            "evidencia": {"texto": texto, "accion": f["accion"],
                          "propuesta_id": f["id"], "pidio": f.get("por"),
                          "aplicado_at": str(f.get("aplicado_at") or ""),
                          "detalle": detalle}}


def _de_rueda(desde, hasta: datetime) -> int:
    """Segundos de MERCADO ABIERTO entre los dos momentos. Reusa el mismo cálculo
    que la frescura de tablas: dos definiciones del horario se separan solas."""
    if desde is None:
        return 0
    from api.services.av_agent_contexto import _segundos_de_rueda
    from core.tz import asegurar_aware
    return _segundos_de_rueda(asegurar_aware(desde), hasta)


def _cuanto(seg: int) -> str:
    return (f"{seg // 3600} h {(seg % 3600) // 60} min" if seg >= 3600
            else f"{seg // 60} min")
