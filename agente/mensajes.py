"""`agente/mensajes.py` — LO QUE EL AGENTE LE MANDA A UNA PERSONA.

Doc: `docs/AGENT_2.0.md` §6.6 (COMUNICACIONES).

**No es un hallazgo.** Un hallazgo es un problema del sistema; esto es un
mensaje dirigido a alguien: «tu cliente quedó descubierto», «Aunesa está caído».
Por eso vive en su propia tabla y no ensucia `agente.hallazgos` — meterlos
juntos fue una de las cosas que hizo ilegible al agente viejo.

**Una comunicación es del DÍA.** No se acumula: si el destinatario no la
atendió, el pendiente es suyo, no del agente.

⚠️ Este módulo NO decide a quién avisar: recibe el email. Quién se encarga de
qué es una decisión de negocio y vive en el job que llama.
"""
from __future__ import annotations

import json
import logging

from core.postgres import get_pool

logger = logging.getLogger(__name__)


def existe(email: str) -> bool:
    """¿Ese email es un usuario del sistema? Mandarle a alguien que no existe es
    escribir para nadie, y hay que enterarse."""
    e = (email or "").strip().lower()
    if not e:
        return False
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM manager.manager_users "
                        "WHERE lower(email) = %s", (e,))
            return cur.fetchone() is not None
    except Exception as ex:
        logger.warning("agente/mensajes: no pude validar %s (%s)", e, ex)
        return True                       # ante la duda, se manda


def enviar(*, para: str, asunto: str, detalle: str = "", tema: str = "mensaje",
           filas: list[dict] | None = None) -> dict:
    """Deja el mensaje. **Idempotente por `(para, tema)`**: el mismo tema el
    mismo día no se duplica — un job que corre cada hora no puede llenarle la
    bandeja a nadie."""
    e = (para or "").strip().lower()
    if not e:
        return {"ok": False, "error": "sin destinatario"}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agente.avisos_dirigidos "
                " (para, tema, asunto, detalle, filas) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (para, tema) DO UPDATE SET "
                "  asunto = EXCLUDED.asunto, detalle = EXCLUDED.detalle, "
                "  filas = EXCLUDED.filas, at = now() RETURNING id",
                (e, tema, asunto, detalle,
                 json.dumps(filas or [], default=str)))
            return {"ok": True, "id": cur.fetchone()[0]}
    except Exception as ex:
        logger.warning("agente/mensajes: no pude mandar a %s (%s)", e, ex)
        return {"ok": False, "error": str(ex)[:200]}


def enviar_tabla(*, para: str, asunto: str, filas: list[dict], tema: str,
                 detalle: str = "") -> dict:
    """Lo mismo, con una tabla adentro. Existe para que el que la recibe pueda
    ver las filas sin que el asunto se convierta en un párrafo."""
    return enviar(para=para, asunto=asunto, detalle=detalle, tema=tema,
                  filas=filas)


def de(email: str, *, solo_hoy: bool = True) -> list[dict]:
    """La bandeja de una persona. Solo la suya: no hay parámetro para pedir la
    de otro, así que no hay nada que forzar."""
    e = (email or "").strip().lower()
    if not e:
        return []
    sql = ("SELECT id, tema, asunto, detalle, filas, at, visto_at "
           "  FROM agente.avisos_dirigidos WHERE lower(para) = %s ")
    if solo_hoy:
        sql += ("AND (at AT TIME ZONE 'America/Argentina/Buenos_Aires')::date "
                "  = (now() AT TIME ZONE 'America/Argentina/Buenos_Aires')::date ")
    sql += "ORDER BY at DESC LIMIT 100"
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (e,))
            return [{"id": r[0], "tema": r[1], "asunto": r[2], "detalle": r[3],
                     "filas": list(r[4] or []), "at": r[5].isoformat(),
                     "visto": r[6] is not None} for r in cur.fetchall()]
    except Exception as ex:
        logger.warning("agente/mensajes: no pude leer la bandeja (%s)", ex)
        return []


def marcar_visto(aviso_id: int, *, quien: str) -> dict:
    """Solo el destinatario puede marcarlo: el `WHERE` lo garantiza."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE agente.avisos_dirigidos SET visto_at = now() "
                    "WHERE id = %s AND lower(para) = %s",
                    (int(aviso_id), (quien or "").strip().lower()))
        return {"ok": bool(cur.rowcount)}
