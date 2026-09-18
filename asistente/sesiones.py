"""Las conversaciones del asistente, guardadas en `ia.conversaciones`: cada una
tiene un dueño, un título, la memoria que ve el modelo (podada) y los turnos
que ve la persona (enteros). Doc: docs/AvAgentAI.md §8."""
from __future__ import annotations

import json
import logging
import re
import uuid
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime

from psycopg import connect
from psycopg.types.json import Jsonb

from asistente import eventos as EV
from asistente import grafo, panel, pantalla
from core.postgres import get_pool, get_postgres_uri

logger = logging.getLogger(__name__)

TITULO_CHARS = 80
LISTA_MAX = 30
_SESION_RE = re.compile(r"[0-9a-f]{32}")


def es_valida(sesion: str | None) -> bool:
    return bool(_SESION_RE.fullmatch(str(sesion or "")))


@contextmanager
def _serializar(sesion: str):
    """Una conversación por vez, incluso con varias réplicas del worker."""
    with ExitStack() as stack:
        try:
            conn = stack.enter_context(connect(get_postgres_uri(), autocommit=True))
            cur = stack.enter_context(conn.cursor())
            cur.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"asistente:{sesion}",))
        except Exception as error:
            logger.warning("sesiones: no pude serializar %s (%s)", sesion, error)
            yield
            return
        try:
            yield
        finally:
            try:
                cur.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                    (f"asistente:{sesion}",),
                )
            except Exception as error:
                logger.warning("sesiones: no pude liberar la serialización de %s (%s)", sesion, error)


def preguntar(pregunta: str, *, usuario: str, sesion: str | None = None,
              rol: str = "admin", portal: str = "trading", run_id: str | None = None,
              grafo_compilado=None, event_sink=None, forzar_sesion: bool = False) -> dict:
    """Una pregunta dentro de una conversación. Sin `sesion` (o con una que no
    es del usuario) empieza una nueva. Nunca levanta: si la base no contesta,
    la pregunta se responde igual, sin memoria, y `guardada` lo dice."""
    pedida = sesion if es_valida(sesion) else None
    aviso_nueva = None
    if pedida:
        with _serializar(pedida):
            previa, aviso = cargar(pedida, usuario)
            if previa or forzar_sesion:
                return _preguntar_guardar(
                    pregunta, usuario=usuario, sesion=pedida, previa=previa, aviso=aviso,
                    rol=rol, portal=portal, run_id=run_id, grafo_compilado=grafo_compilado,
                    event_sink=event_sink,
                )
            aviso_nueva = aviso
    nueva = uuid.uuid4().hex
    with _serializar(nueva):
        return _preguntar_guardar(
            pregunta, usuario=usuario, sesion=nueva, previa=None, aviso=aviso_nueva,
            rol=rol, portal=portal, run_id=run_id, grafo_compilado=grafo_compilado,
            event_sink=event_sink,
        )


def _preguntar_guardar(pregunta: str, *, usuario: str, sesion: str, previa: dict | None,
                       aviso: str | None, rol: str, portal: str, run_id: str | None,
                       grafo_compilado, event_sink) -> dict:
    with EV.capturar(event_sink):
        r = grafo.preguntar(
            pregunta, usuario=usuario, historial=previa["memoria"] if previa else [],
            estado=previa["foco"] if previa else {}, sesion=sesion,
            rol=rol, portal=portal, run_id=run_id, grafo_compilado=grafo_compilado)
    turno = {"run_id": run_id, "pregunta": pregunta, "respuesta": r["respuesta"], "falta": r["falta"],
             "error": r["error"], "agentes": r["agentes"], "tablas": tablas_de(r["eventos"]),
             "at": datetime.now(UTC).isoformat(timespec="seconds")}
    anteriores = previa["turnos"] if previa else []
    turnos = [t for t in anteriores if not run_id or t.get("run_id") != run_id] + [turno]
    titulo = previa["titulo"] if previa else _titulo(pregunta)
    guardada = guardar(r["sesion"], usuario, titulo=titulo, memoria=r["mensajes"],
                       foco=r["estado"], turnos=turnos)
    return {**r, "titulo": titulo, "guardada": guardada, "aviso": aviso,
            "sesion": panel.conversacion(r["sesion"])}


def tablas_de(eventos: list[dict]) -> list[dict]:
    """Las tablas que declararon las herramientas de un turno, para que una
    conversación reabierta las muestre igual. El criterio no se reimplementa
    acá: es el mismo `pantalla.para_dibujar` que arma la tabla en vivo."""
    return [t for e in eventos or []
            if e.get("tipo") == "resultado"
            and (t := pantalla.para_dibujar(e.get("resultado"))) is not None]


def _titulo(pregunta: str) -> str:
    t = " ".join(pregunta.split())
    return t if len(t) <= TITULO_CHARS else t[:TITULO_CHARS - 1] + "…"


# ── la tabla ────────────────────────────────────────────────────────────────


def cargar(sesion: str, usuario: str) -> tuple[dict | None, str | None]:
    """(conversación, aviso). None si no existe o no es de este usuario; el
    aviso se llena si la base no contestó."""
    if not es_valida(sesion):
        return None, None
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT sesion, titulo, memoria, foco, turnos FROM ia.conversaciones"
                " WHERE sesion = %s AND usuario = %s", (sesion, usuario))
            fila = cur.fetchone()
    except Exception as e:
        logger.warning("sesiones: no pude leer %s (%s)", sesion, e)
        return None, f"no pude leer la conversación guardada: la contesto sin memoria ({e})"
    if not fila:
        return None, None
    return {"sesion": fila[0], "titulo": fila[1], "memoria": list(fila[2] or []),
            "foco": dict(fila[3] or {}), "turnos": list(fila[4] or [])}, None


def guardar(sesion: str, usuario: str, *, titulo: str, memoria: list[dict], foco: dict,
            turnos: list[dict]) -> bool:
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ia.conversaciones (sesion, usuario, titulo, memoria, foco, turnos,"
                " preguntas) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (sesion) DO UPDATE SET memoria = EXCLUDED.memoria,"
                " foco = EXCLUDED.foco, turnos = EXCLUDED.turnos,"
                " preguntas = EXCLUDED.preguntas, actualizada_at = now()"
                " WHERE ia.conversaciones.usuario = EXCLUDED.usuario",
                (sesion, usuario, titulo, _json(memoria), _json(foco), _json(turnos),
                 len(turnos)))
            # 0 filas = la sesión existe con otro dueño y el WHERE la protegió.
            # Decir «guardada» acá sería mentir.
            return cur.rowcount > 0
    except Exception as e:
        logger.warning("sesiones: no pude guardar %s (%s)", sesion, e)
        return False


def listar(usuario: str, limite: int = LISTA_MAX) -> dict:
    """Las conversaciones del usuario, la más reciente primero, con los tokens
    que llevan gastados (sumados de `ia.llamadas`)."""
    sql = """
        SELECT c.sesion, c.titulo, c.preguntas, c.creada_at, c.actualizada_at,
               coalesce(sum(l.tokens_in), 0) + coalesce(sum(l.tokens_out), 0) AS tokens
          FROM ia.conversaciones c
          LEFT JOIN ia.llamadas l ON l.sesion = c.sesion
         WHERE c.usuario = %(u)s
         GROUP BY c.sesion
         ORDER BY c.actualizada_at DESC
         LIMIT %(n)s
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql, {"u": usuario, "n": max(1, min(int(limite), 200))})
            filas = cur.fetchall()
    except Exception as e:
        return {"conversaciones": [], "error": f"no pude listar: {type(e).__name__}: {e}"}
    return {"conversaciones": [{
        "sesion": s, "titulo": t, "preguntas": int(n),
        "creada_at": c.isoformat(), "actualizada_at": a.isoformat(), "tokens": int(tok),
    } for s, t, n, c, a, tok in filas]}


def abrir(sesion: str, usuario: str) -> dict | None:
    """Una conversación entera para volver a mostrarla: sus turnos, el foco y el
    costo. None si no existe o no es del usuario."""
    c, aviso = cargar(sesion, usuario)
    if not c:
        return None
    return {"sesion": c["sesion"], "titulo": c["titulo"], "turnos": c["turnos"],
            "estado": c["foco"], "costo": panel.conversacion(c["sesion"]), "aviso": aviso}


def borrar(sesion: str, usuario: str) -> dict:
    if not es_valida(sesion):
        return {"ok": False, "error": "sesión inválida"}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT run_id FROM ia.ejecuciones WHERE sesion = %s AND usuario = %s",
                (sesion, usuario),
            )
            run_ids = [row[0] for row in cur.fetchall()]
        from asistente import checkpoints
        checkpoints.borrar_threads(run_ids)
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM ia.ejecuciones WHERE sesion = %s AND usuario = %s",
                (sesion, usuario),
            )
            cur.execute("DELETE FROM ia.conversaciones WHERE sesion = %s AND usuario = %s",
                        (sesion, usuario))
            n = cur.rowcount
    except Exception as e:
        return {"ok": False, "error": f"no pude borrar: {type(e).__name__}: {e}"}
    return {"ok": n > 0, "borradas": n}


def _json(v) -> Jsonb:
    return Jsonb(v, dumps=lambda x: json.dumps(x, ensure_ascii=False, default=str))
