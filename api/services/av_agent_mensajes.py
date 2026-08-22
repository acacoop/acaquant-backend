"""api/services/av_agent_mensajes.py — EL AGENTE SABE MANDAR UN MENSAJE.

Doc madre: **`docs/AV_AGENT.md`** §0.ab.

Pedido del user (2026-08-19): *«necesito que el agente mejore lo de enviar
mensajes al resto de usuarios… más allá de dejar esta función, estaría bueno que
sea algo propio y fácil del agente, ya que lo quiero usar para nuevas cosas»*.

DE DÓNDE VIENE
==============

Mandar un ping existía **encapsulado adentro de una acción** (`avisar.responsable`,
que solo sabía avisar del control `comitentes_sin_nivel1`). Para usarlo en otra
cosa había que escribir otra acción, así que en la práctica no se usaba para nada
más — y encima estaba roto: el índice único ignoraba el destinatario, con lo cual
el primer ping sobre un tema **bloqueaba en silencio** todos los siguientes a
cualquier otra persona.

Acá el mensaje pasa a ser una capacidad de primera clase: cualquier job, control
o acción manda uno sin saber nada del buzón.

DOS COSAS QUE HACEN QUE SIRVA, Y NO SON OBVIAS
===============================================

1. **NO se crea un buzón nuevo.** Es la misma `agente.av_agent_avisos` que la
   persona ya ve en su barra (`MisAvisos`), se cierra igual y se audita igual. Un
   segundo lugar donde mirar lo que hay para hacer es exactamente el problema que
   SALUD vino a resolver cuando la observabilidad estaba en seis pantallas.

2. **El agente sabe QUIÉN ES QUIÉN.** Un mensaje a `operador@` no se puede pedir
   si hay que averiguar el mail a mano cada vez. `directorio()` resuelve
   operadores → email desde `clientes.operadores`, y `de_quien_es()` dice qué
   operador atiende cada cuenta comitente. Todo sale de la base: **no hay una
   sola lista escrita a mano acá**.

LO QUE NO HACE, Y ES A PROPÓSITO
=================================

No manda mail ni push. El mensaje vive **adentro de la app**, donde la persona ya
está y donde puede cerrarlo. Un canal externo es otra decisión (y otro riesgo de
privacidad) — cuando haga falta, se suma acá y los que llaman no cambian.
"""
from __future__ import annotations

import logging

from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Cuántos destinatarios distintos puede tener un envío. No es una limitación
# técnica: es que **un mensaje a 200 personas no es un mensaje, es spam interno**,
# y el primero que lo reciba sin esperarlo deja de mirar la campanita.
MAX_DESTINATARIOS = 60


def directorio() -> dict[str, str]:
    """`email → nombre` de los OPERADORES. Sale de `clientes.operadores`.

    Es lo que le permite al agente mandarle algo «al operador de esta cuenta» sin
    que nadie tenga que saber su mail. Vacío si no se puede leer — y quien llame
    tiene que notar la diferencia entre «no hay operadores» y «no pude mirar».
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT lower(email), coalesce(nombre, '') "
                        "FROM clientes.operadores WHERE email IS NOT NULL")
            return {a: b for a, b in cur.fetchall() if a}
    except Exception as e:
        logger.warning("av_agent_mensajes: no pude leer el directorio (%s)", e)
        return {}


def de_quien_es(cuentas: list[str]) -> dict[str, str]:
    """`id_cuenta → email del operador que la atiende`.

    Las cuentas sin operador **no entran al dict**, y eso es información: son las
    que no le llegarían a nadie. Quien llame tiene que decidir qué hace con ellas
    en vez de que desaparezcan.
    """
    if not cuentas:
        return {}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id_cuenta, lower(operador_email) FROM clientes.comitentes "
                "WHERE id_cuenta = ANY(%s) AND operador_email IS NOT NULL",
                (list(cuentas),))
            return {a: b for a, b in cur.fetchall() if b}
    except Exception as e:
        logger.warning("av_agent_mensajes: no pude resolver operadores (%s)", e)
        return {}


def existe(email: str) -> bool:
    """¿Es un usuario de la plataforma? Mandar a un mail que no entra a la app es
    escribir en un buzón que nadie abre."""
    e = (email or "").strip().lower()
    if "@" not in e:
        return False
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM manager.manager_users "
                        "WHERE lower(email) = %s LIMIT 1", (e,))
            return cur.fetchone() is not None
    except Exception as ex:
        logger.warning("av_agent_mensajes: no pude validar %s (%s)", e, ex)
        # **Ante la duda, se manda.** Un mensaje de más se cierra; uno de menos
        # no existe y nadie se entera de que faltó.
        return True


def enviados_sobre(ticker: str, limite: int = 20) -> list[dict]:
    """**LA MEMORIA DE LO QUE YA SE AVISÓ.** Todo lo mandado sobre un tema, esté
    abierto o cerrado: `[{para, creado_at, resuelto, resuelto_at, resuelto_por}]`.

    ⚠️ Por qué existe (user, 2026-08-22, sobre `comitentes_sin_nivel1`): *«yo
    antes ya le mandé el mail pero no me dice AVISADO A LA PERSONA, y además no
    detecta bien qué avisó y qué no… dice IR A ARREGLARLO, esperando… no es
    claro»*.

    La tarjeta ofrecía «avisarle a la persona» **con el aviso ya mandado y
    esperando en su bandeja**. No estaba rota: nunca miraba para atrás. Y sin
    esa mirada las dos únicas salidas eran igual de malas — o volvés a mandar lo
    mismo (un mensaje repetido informa MENOS), o no lo mandás por las dudas.

    Se lee de la MISMA tabla que la campanita de la persona (`av_agent_avisos`):
    no hay un registro nuevo de «qué avisé», así que no puede contradecir a lo
    que el destinatario efectivamente tiene.
    """
    t = (ticker or "").strip()
    if not t:
        return []
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT lower(coalesce(para, '')), creado_at::text, resuelto, "
                "       resuelto_at::text, coalesce(resuelto_por, '') "
                "FROM agente.av_agent_avisos WHERE ticker = %s "
                "ORDER BY creado_at DESC LIMIT %s", (t, limite))
            return [{"para": a, "creado_at": b, "resuelto": c,
                     "resuelto_at": d, "resuelto_por": e}
                    for a, b, c, d, e in cur.fetchall() if a]
    except Exception as e:
        logger.warning("av_agent_mensajes: no pude leer lo avisado de %s (%s)", t, e)
        # ⚠️ Lista VACÍA por no haber podido mirar se leería como «no avisé
        # nunca». Quien llama distingue el caso mirando el log; acá no se
        # inventa un `[]` silencioso con otro significado.
        return []


def enviar(*, para: str, asunto: str, detalle: str = "", tema: str = "mensaje",
           donde: str = "", por: str = "av-agent") -> dict:
    """UN mensaje a UNA persona. Devuelve `{ok, creado, error}`.

    `tema` es la IDENTIDAD del mensaje para esa persona: dos envíos del mismo
    tema no se apilan mientras el primero siga abierto. Es lo que evita que un
    job diario deje 30 copias de lo mismo — un mensaje repetido no informa más,
    informa menos.

    **Nunca levanta.** Un mensaje que no se pudo mandar no puede tumbar al job
    que lo estaba mandando; se devuelve el motivo y el que llama decide.
    """
    from api.services import av_agent_vista
    e = (para or "").strip().lower()
    if "@" not in e:
        return {"ok": False, "error": "destinatario inválido"}
    if not (asunto or "").strip():
        return {"ok": False, "error": "un mensaje sin asunto no se lee"}
    if not existe(e):
        return {"ok": False, "error": f"«{e}» no es un usuario de la plataforma"}
    try:
        n = av_agent_vista.avisar_a(
            para=e, ticker=f"tema:{tema}", clave=tema,
            que_hacer=asunto, por_que=detalle, donde=donde, por=por)
    except Exception as ex:
        return {"ok": False, "error": f"{type(ex).__name__}: {str(ex)[:160]}"}
    # `creado=False` NO es un error: significa que esa persona ya tiene este
    # mismo mensaje abierto. Distinguirlo del fallo es lo que permite que un job
    # diario corra tranquilo sin ensuciar el log.
    return {"ok": True, "creado": bool(n), "para": e, "tema": tema}


def enviar_tabla(*, para: str, asunto: str, filas: list[dict], tema: str,
                 detalle: str = "", donde: str = "", por: str = "av-agent",
                 interrumpe: bool = True, vence_en_horas: int = 0) -> dict:
    """Un mensaje que trae una TABLA y **pide que la completes**.

    El user, sobre el aviso de saldos: *«tiene que ser como el modal de briefing:
    aparece en la pantalla, llama la atención y te hace hacer algo para
    continuar. No que aparezca en el cuerpo del agente como si nada.»*

    Cada fila de `filas` es `{clave, etiqueta, datos}` y se tilda por separado,
    con quién y cuándo. `clave` es su identidad: re-mandar el mismo tema **no
    pisa las tildes que ya se pusieron** — el operador que ya marcó tres no las
    pierde porque el job volvió a correr.

    `vence_en_horas=0` calcula el vencimiento al **próximo cierre de jornada**
    (medianoche ART): un aviso de saldos vale HOY, y mañana el mercado abre con
    otros números. Un aviso vencido pidiendo acción es peor que ninguno.
    """
    from datetime import UTC, datetime, timedelta

    e = (para or "").strip().lower()
    if "@" not in e:
        return {"ok": False, "error": "destinatario inválido"}
    if not filas:
        return {"ok": False, "error": "una tabla sin filas no es un mensaje"}
    if not existe(e):
        return {"ok": False, "error": f"«{e}» no es un usuario de la plataforma"}

    if vence_en_horas > 0:
        vence = datetime.now(UTC) + timedelta(hours=vence_en_horas)
    else:
        # Medianoche ART = 03:00 UTC del día siguiente.
        ahora = datetime.now(UTC)
        vence = (ahora + timedelta(days=1)).replace(hour=3, minute=0, second=0,
                                                    microsecond=0)
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agente.av_agent_avisos "
                "(ticker, clave, que_hacer, por_que, donde, creado_por, para, "
                " interrumpe, vence_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING RETURNING id",
                (f"tema:{tema}", tema, asunto[:500], (detalle or "")[:300],
                 donde or None, por or None, e, interrumpe, vence))
            fila = cur.fetchone()
            if fila:
                aviso_id, creado = fila[0], True
            else:
                # Ya existía abierto: se REUSA y se actualizan las filas nuevas
                # sin tocar las que ya están tildadas.
                cur.execute(
                    "SELECT id FROM agente.av_agent_avisos "
                    "WHERE NOT resuelto AND lower(para) = %s AND clave = %s "
                    "  AND ticker = %s", (e, tema, f"tema:{tema}"))
                r = cur.fetchone()
                if not r:
                    return {"ok": False, "error": "no se pudo crear ni encontrar "
                                                  "el aviso"}
                aviso_id, creado = r[0], False
            cur.executemany(
                "INSERT INTO agente.av_agent_aviso_items "
                "(aviso_id, orden, clave, etiqueta, datos) "
                "VALUES (%s, %s, %s, %s, %s::jsonb) "
                # **NO se pisa `hecho`.** El operador que ya marcó tres no las
                # pierde porque el job volvió a correr.
                "ON CONFLICT (aviso_id, clave) DO UPDATE SET "
                "  etiqueta = EXCLUDED.etiqueta, datos = EXCLUDED.datos, "
                "  orden = EXCLUDED.orden",
                [(aviso_id, i, str(f.get("clave") or i),
                  str(f.get("etiqueta") or "")[:300],
                  __import__("json").dumps(f.get("datos") or {}, default=str))
                 for i, f in enumerate(filas)])
            conn.commit()
    except Exception as ex:
        logger.exception("av_agent_mensajes: no se pudo mandar la tabla")
        return {"ok": False, "error": f"{type(ex).__name__}: {str(ex)[:160]}"}
    _espejar_filas(tema, filas, e)
    return {"ok": True, "creado": creado, "aviso_id": aviso_id, "para": e,
            "filas": len(filas), "vence_at": vence.isoformat()}


# ── CADA FILA DE UN AVISO TAMBIÉN ES UN OBJETO (§0.bk) ──────────────────────
#
# «La cuenta 805 está descubierta en ARS» es **(qué cosa, qué le pasa)**: la
# misma identidad que un hallazgo. Espejar fila por fila —y no el aviso entero—
# es lo que permite la frase que hoy no se puede decir: *esta cuenta lleva
# CUATRO DÍAS descubierta*, o *volvió a estarlo después de que la cerraste*. El
# aviso agrupado no puede saberlo: cada vez que el job corre lo rearma.
#
# ⚠️ La tabla `av_agent_aviso_items` no se toca: sigue siendo la que guarda la
# tilde auditable (quién y cuándo). El objeto acompaña, y si el espejo falla el
# mensaje se manda igual.


def _espejar_filas(tema: str, filas: list[dict], para: str) -> None:
    """Las filas de un mensaje, como objetos. Nunca levanta."""
    try:
        from api.services import av_agent_items
        for f in filas or []:
            sujeto = str(f.get("clave") or "").strip()
            if not sujeto:
                continue
            av_agent_items.ver(
                tipo="aviso_fila", origen="mensaje", sujeto=sujeto, regla=tema,
                titulo=str(f.get("etiqueta") or "")[:300],
                afecta=f"le toca a {para}", severidad="media",
                datos=f.get("datos") or {})
    except Exception as e:                  # pragma: no cover - defensivo
        logger.warning("av_agent_mensajes: no pude espejar las filas de %s (%s)",
                       tema, e)


def _cerrar_fila(item_id: int, *, por: str, hecho: bool) -> None:
    """La tilde, en el objeto. **Destildar es `volvio`**: el vocabulario solo
    deja salir de `resuelto` por ahí, y además es lo que pasó — alguien lo
    había dado por hecho y sigue pendiente."""
    try:
        from api.services import av_agent_items
        from core import ciclo
        from core.postgres import get_pool as _pool
        with _pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT i.clave, a.clave FROM agente.av_agent_aviso_items i "
                        "JOIN agente.av_agent_avisos a ON a.id = i.aviso_id "
                        "WHERE i.id = %s", (item_id,))
            r = cur.fetchone()
        if not r:
            return
        k = av_agent_items.clave_de_problema(r[0], r[1], "mensaje")
        av_agent_items.marcar(k, ciclo.RESUELTO if hecho else ciclo.VOLVIO,
                              por=por or "persona")
    except Exception as e:                  # pragma: no cover - defensivo
        logger.warning("av_agent_mensajes: no pude cerrar la fila %s (%s)",
                       item_id, e)


def marcar_item(item_id: int, *, quien: str, hecho: bool = True) -> dict:
    """Tilda (o destilda) UNA fila. **Solo si el aviso es de esa persona.**

    El `AND lower(a.para) = %s` va en el WHERE y no en un `if` previo, igual que
    en `resolver_aviso_propio`: así «es mío» no es un permiso que alguien pueda
    olvidarse de chequear en el próximo endpoint — es parte de la escritura.
    """
    q = (quien or "").strip().lower()
    if not q:
        return {"ok": False, "error": "sin usuario"}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE agente.av_agent_aviso_items i "
                "SET hecho = %s, hecho_at = CASE WHEN %s THEN now() END, "
                "    hecho_por = CASE WHEN %s THEN %s END "
                "FROM agente.av_agent_avisos a "
                "WHERE i.id = %s AND a.id = i.aviso_id AND lower(a.para) = %s "
                "RETURNING i.aviso_id",
                (hecho, hecho, hecho, q, item_id, q))
            r = cur.fetchone()
            if not r:
                return {"ok": False, "error": "no existe esa fila"}
            # Si NO queda ninguna pendiente, el aviso se cierra solo: pedirle
            # además que apriete «listo» sería un paso que no agrega nada.
            cur.execute(
                "UPDATE agente.av_agent_avisos SET resuelto = true, "
                "  resuelto_por = %s, resuelto_at = now() "
                "WHERE id = %s AND NOT resuelto AND NOT EXISTS ("
                "  SELECT 1 FROM agente.av_agent_aviso_items "
                "  WHERE aviso_id = %s AND NOT hecho)", (q, r[0], r[0]))
            cerrado = cur.rowcount > 0
            conn.commit()
        _cerrar_fila(item_id, por=q, hecho=hecho)
        return {"ok": True, "hecho": hecho, "aviso_cerrado": cerrado}
    except Exception as ex:
        return {"ok": False, "error": f"{type(ex).__name__}: {str(ex)[:160]}"}


def enviar_muchos(mensajes: list[dict], *, por: str = "av-agent") -> dict:
    """Varios mensajes de una. Cada uno con su destinatario y su cuerpo.

    Devuelve el recuento **y los que fallaron con su motivo**: un envío masivo
    que solo dice «mandé 12» esconde a los 3 que no llegaron, que son justo los
    que hay que mirar.
    """
    mensajes = mensajes or []
    destinos = {(m.get("para") or "").strip().lower() for m in mensajes}
    if len(destinos) > MAX_DESTINATARIOS:
        return {"ok": False, "enviados": 0,
                "error": f"{len(destinos)} destinatarios (máx {MAX_DESTINATARIOS}): "
                         f"un mensaje a todos no es un mensaje, es spam interno"}
    enviados = repetidos = 0
    fallaron: list[dict] = []
    for m in mensajes:
        r = enviar(para=m.get("para") or "", asunto=m.get("asunto") or "",
                   detalle=m.get("detalle") or "", tema=m.get("tema") or "mensaje",
                   donde=m.get("donde") or "", por=por)
        if not r.get("ok"):
            fallaron.append({"para": m.get("para"), "error": r.get("error")})
        elif r.get("creado"):
            enviados += 1
        else:
            repetidos += 1
    return {"ok": True, "enviados": enviados, "ya_estaban": repetidos,
            "fallaron": fallaron, "destinatarios": len(destinos)}
