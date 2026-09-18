"""Persistencia de runs, eventos SSE y evidencias del asistente."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from asistente import ejecutor as EXE
from asistente import eventos as EV
from asistente import sesiones
from core.postgres import get_pool

TERMINALES = {"succeeded", "failed", "cancelled", "timed_out"}
ACTIVOS = {"queued", "running", "waiting_approval", "cancel_requested"}


def crear(pregunta: str, *, usuario: str, rol: str, portal: str,
          sesion: str | None = None, tipo: str = "pregunta",
          origen: str = "daemon") -> dict:
    """Crea un run queued y decide su sesión sin aceptar una sesión ajena.
    `tipo`: `pregunta` (una conversación) o `diagnostico` (lo dispara el AV
    AGENT sobre un hallazgo; el worker lo despacha distinto).
    `origen`: QUIÉN lo pidió — `daemon`, el email de la persona, o `consola`.
    El default es `daemon` a propósito: lo que no dice quién lo pidió se trata
    como automático, y eso no corre con el interruptor del LAB apagado."""
    sesion_valida = None
    if sesion and sesiones.es_valida(sesion):
        previa, _ = sesiones.cargar(sesion, usuario)
        sesion_valida = previa["sesion"] if previa else None
    run_id = uuid.uuid4().hex
    sesion_id = sesion_valida or uuid.uuid4().hex
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "INSERT INTO ia.ejecuciones (run_id, sesion, usuario, rol, portal, pregunta, tipo,"
            " origen) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (run_id, sesion_id, usuario, rol, portal, pregunta, tipo,
             (origen or "").strip() or "daemon"),
        )
        return _publica(cur.fetchone())


def obtener(run_id: str, usuario: str | None = None) -> dict | None:
    where = "run_id = %s"
    params: list = [run_id]
    if usuario is not None:
        where += " AND usuario = %s"
        params.append(usuario)
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT * FROM ia.ejecuciones WHERE {where}", tuple(params))
        row = cur.fetchone()
    return _publica(row) if row else None


def reclamar() -> dict | None:
    """Toma un run queued sin competir con otro worker."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "WITH candidato AS ("
            " SELECT run_id FROM ia.ejecuciones WHERE estado = 'queued'"
            " ORDER BY creada_at FOR UPDATE SKIP LOCKED LIMIT 1"
            ") UPDATE ia.ejecuciones e SET estado = 'running', iniciada_at = coalesce(iniciada_at, now()),"
            " actualizada_at = now(), intentos = intentos + 1 FROM candidato c"
            " WHERE e.run_id = c.run_id RETURNING e.*"
        )
        row = cur.fetchone()
    return dict(row) if row else None


def reencolar_interrumpidas() -> int:
    """Al arrancar el único worker, reanuda lo que quedó running tras un corte."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE ia.ejecuciones SET estado = 'queued', actualizada_at = now(),"
            " error = 'worker reiniciado: reanudando desde checkpoint' WHERE estado = 'running'"
        )
        return cur.rowcount


def emitir(run_id: str, evento: dict) -> int:
    """Persiste un evento y sus evidencias. Corta cooperativamente si se canceló."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT estado FROM ia.ejecuciones WHERE run_id = %s", (run_id,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"no existe el run {run_id}")
        if row[0] == "cancel_requested":
            raise EV.CancelacionSolicitada(run_id)
        evidencias = _evidencias_de(evento)
        persistible = _persistible(evento)
        payload = Jsonb(persistible, dumps=lambda value: json.dumps(
            value, ensure_ascii=False, default=str))
        cur.execute(
            "INSERT INTO ia.eventos_ejecucion (run_id, tipo, agente, payload)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (run_id, str(evento.get("tipo") or "evento"), evento.get("agente"), payload),
        )
        evento_id = int(cur.fetchone()[0])
        for evidencia in evidencias:
            campos = _campos_persistibles(evidencia)
            cur.execute(
                "INSERT INTO ia.evidencias"
                " (run_id, ref, herramienta, acceso, sujeto, fecha, ruta, campos)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (run_id, evidencia["ref"], evidencia["herramienta"], evidencia["acceso"],
                 evidencia["sujeto"], evidencia.get("fecha"), evidencia["ruta"],
                 Jsonb(campos, dumps=lambda value: json.dumps(
                     value, ensure_ascii=False, default=str))),
            )
        return evento_id


def eventos_desde(run_id: str, usuario: str, despues_de: int = 0) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT ev.id, ev.ts, ev.payload FROM ia.eventos_ejecucion ev"
            " JOIN ia.ejecuciones r ON r.run_id = ev.run_id"
            " WHERE ev.run_id = %s AND r.usuario = %s AND ev.id > %s ORDER BY ev.id LIMIT 200",
            (run_id, usuario, max(0, int(despues_de))),
        )
        return [{"id": int(row["id"]), "ts": row["ts"].isoformat(), **dict(row["payload"] or {})}
                for row in cur.fetchall()]


def eventos_de(run_id: str, limite: int = 500) -> list[dict]:
    """El ciclo entero de un run SIN filtro de dueño: para los runs del agente
    (`av-agent`), que una persona mira desde el LAB. La ruta que lo sirve es
    admin-only; no usar desde nada que un usuario alcance con su propio id."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, ts, payload FROM ia.eventos_ejecucion WHERE run_id = %s"
            " ORDER BY id LIMIT %s", (run_id, max(1, min(int(limite), 2000))))
        return [{"id": int(row["id"]), "ts": row["ts"].isoformat(), **dict(row["payload"] or {})}
                for row in cur.fetchall()]


def runs_de(pregunta: str, limite: int = 20) -> list[dict]:
    """Los runs con esa pregunta exacta, el más nuevo primero (un diagnóstico
    se identifica por su pregunta `diagnosticar hallazgo #N`)."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT run_id, tipo, estado, error, origen, creada_at, iniciada_at, finalizada_at,"
            " intentos FROM ia.ejecuciones WHERE pregunta = %s ORDER BY creada_at DESC LIMIT %s",
            (pregunta, max(1, min(int(limite), 100))))
        return [_publica(row) for row in cur.fetchall()]


def runs_de_tipo(tipo: str, limite: int = 50) -> list[dict]:
    """Los runs de un tipo (`diagnostico`), el más nuevo primero, con su
    resultado. Sin filtro de dueño: es para los runs del agente, servidos por
    una ruta admin-only."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT run_id, tipo, estado, error, origen, pregunta, resultado, creada_at,"
            " iniciada_at, finalizada_at, intentos FROM ia.ejecuciones WHERE tipo = %s"
            " ORDER BY creada_at DESC LIMIT %s", (tipo, max(1, min(int(limite), 200))))
        return [_publica(row) for row in cur.fetchall()]


def terminar(run_id: str, estado: str, *, resultado: dict | None = None,
             error: str | None = None) -> None:
    if estado not in TERMINALES:
        raise ValueError(f"estado terminal inválido: {estado}")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE ia.ejecuciones SET estado = %s, resultado = %s, error = %s,"
            " finalizada_at = now(), actualizada_at = now() WHERE run_id = %s",
            (estado, _json(resultado) if resultado is not None else None, error, run_id),
        )


def cancelar(run_id: str, usuario: str) -> dict | None:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "UPDATE ia.ejecuciones SET estado = CASE WHEN estado = 'queued' THEN 'cancelled'"
            " ELSE 'cancel_requested' END, cancel_solicitada_at = now(), actualizada_at = now(),"
            " finalizada_at = CASE WHEN estado = 'queued' THEN now() ELSE finalizada_at END"
            " WHERE run_id = %s AND usuario = %s AND estado IN ('queued', 'running', 'waiting_approval')"
            " RETURNING *",
            (run_id, usuario),
        )
        row = cur.fetchone()
    salida = _publica(row) if row else obtener(run_id, usuario)
    if salida and salida.get("estado") == "cancelled":
        emitir(run_id, {"tipo": "cancelled", "agente": "run"})
    return salida


def metricas(dias: int = 30, *, atascado_min: int = 15) -> dict:
    """Resumen operativo para el panel y el AV Agent."""
    ventana = max(1, min(int(dias), 365))
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT estado, count(*) AS n,"
            " percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM"
            " (finalizada_at - iniciada_at)) * 1000) FILTER (WHERE finalizada_at IS NOT NULL) AS p50_ms,"
            " percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM"
            " (finalizada_at - iniciada_at)) * 1000) FILTER (WHERE finalizada_at IS NOT NULL) AS p95_ms"
            " FROM ia.ejecuciones WHERE creada_at >= now() - make_interval(days => %s) GROUP BY estado",
            (ventana,),
        )
        estados = [dict(row) for row in cur.fetchall()]
        cur.execute(
            "SELECT payload->>'herramienta' AS herramienta, count(*) AS ejecuciones,"
            " avg((payload->>'duracion_ms')::numeric)::float AS latencia_media_ms,"
            " count(*) FILTER (WHERE payload->'resultado' ? 'error') AS errores"
            " FROM ia.eventos_ejecucion WHERE tipo = 'resultado'"
            " AND ts >= now() - make_interval(days => %s) GROUP BY 1 ORDER BY ejecuciones DESC",
            (ventana,),
        )
        tools = [dict(row) for row in cur.fetchall()]
        cur.execute(
            "SELECT count(*) FILTER (WHERE resultado->'control'->>'ok' = 'false') AS sin_evidencia,"
            " count(*) AS terminadas FROM ia.ejecuciones WHERE estado = 'succeeded'"
            " AND creada_at >= now() - make_interval(days => %s)",
            (ventana,),
        )
        calidad = dict(cur.fetchone() or {})
        cur.execute(
            "SELECT count(*) AS atascadas FROM ia.ejecuciones"
            " WHERE estado IN ('queued', 'running', 'cancel_requested')"
            " AND actualizada_at < now() - make_interval(mins => %s)",
            (max(1, min(int(atascado_min), 1440)),),
        )
        atascadas = int(cur.fetchone()["atascadas"] or 0)
    por_estado = {fila["estado"]: int(fila["n"]) for fila in estados}
    resumen = {
        "total": sum(por_estado.values()),
        "succeeded": por_estado.get("succeeded", 0),
        "failed": por_estado.get("failed", 0),
        "cancelled": por_estado.get("cancelled", 0),
        "timed_out": por_estado.get("timed_out", 0),
        "atascadas": atascadas,
        "control_fallido": int(calidad.get("sin_evidencia") or 0),
        "p95_ms": max((float(fila.get("p95_ms") or 0) for fila in estados), default=0),
    }
    return {"dias": ventana, "estados": estados, "herramientas": tools,
            "atascadas": atascadas, "resumen": resumen, **calidad}


def _publica(row: dict | None) -> dict:
    if not row:
        return {}
    salida = dict(row)
    for clave, valor in list(salida.items()):
        if isinstance(valor, datetime):
            salida[clave] = valor.astimezone(UTC).isoformat()
    return salida


def _json(value) -> Jsonb:
    return Jsonb(value, dumps=lambda item: json.dumps(item, ensure_ascii=False, default=str))


def _evidencias_de(evento: dict) -> list[dict]:
    resultado = evento.get("resultado")
    if not isinstance(resultado, dict):
        return []
    return list(resultado.get("_evidencias") or [])


def _persistible(evento: dict) -> dict:
    """La auditoría personal guarda forma y tiempos, nunca valores ni argumentos."""
    salida = dict(evento)
    nombre = str(salida.get("herramienta") or "")
    acceso = salida.get("acceso") or EXE.acceso_de(nombre)
    valor_acceso = acceso.value if isinstance(acceso, EXE.Acceso) else acceso
    if valor_acceso != EXE.Acceso.READ_PERSONAL.value:
        return salida
    if "argumentos" in salida:
        salida["argumentos"] = {"redactado": True}
    resultado = salida.get("resultado")
    if isinstance(resultado, dict):
        campos = sorted(str(campo) for campo in resultado if not str(campo).startswith("_"))
        salida["resultado"] = {"redactado": True, "campos": campos}
    return salida


def _campos_persistibles(evidencia: dict) -> dict:
    campos = dict(evidencia.get("campos") or {})
    if evidencia.get("acceso") == EXE.Acceso.READ_PERSONAL.value:
        return {str(campo): None for campo in campos}
    return campos
