"""Idempotencia de envío de órdenes — anti doble-orden (reintento / doble-click).

Cada intención de orden lleva una clave única (`client_order_id`). El PRIMER
envío que reserva la clave manda al broker; un reenvío con la MISMA clave NO
manda otra orden: devuelve el resultado del primero. Una orden genuinamente
distinta lleva otra clave → nunca se bloquea. Sin clave, este módulo no se
invoca y el envío es idéntico al de siempre (100% retrocompatible).

`Operaciones.OrdenesIdempotency`: índice único en `key` (atómico ante doble
submit SIMULTÁNEO) + TTL de 1 día (las claves solo viven para atajar
reintentos; no crece infinito).

Degradación segura: ante cualquier error de infra, preferimos MANDAR la orden
(retornar como si reserváramos) antes que tragarla. La seguridad es contra
duplicados accidentales, no a costa de perder una orden real.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from core.postgres import get_pool

logger = logging.getLogger(__name__)

# SQL-native (decomiso 2026-06-29): el dedup vive en operaciones.ordenes_idempotency
# (clave PK = atómico ante doble-submit, igual que el unique index Mongo). Postgres no
# tiene TTL index → prune por `ts` en _ensure (las claves solo atajan reintentos).
_TTL_S = 86_400      # 1 día
_WAIT_S = 4.0        # espera máx. del resultado del 1er envío en una dup concurrente
_POLL_S = 0.1


def _ensure() -> None:
    """Self-create de la tabla + prune del TTL (best-effort)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS operaciones.ordenes_idempotency ("
            "clave text PRIMARY KEY, ts timestamptz DEFAULT now(), data jsonb)")
        cur.execute("DELETE FROM operaciones.ordenes_idempotency "
                    "WHERE ts < now() - make_interval(secs => %s)", (_TTL_S,))
        conn.commit()


def reservar(key: str) -> bool:
    """True si reservamos la clave (1er envío → hay que mandar). False si ya existía
    (duplicado → NO mandar). Atómico vía PK + ON CONFLICT DO NOTHING. Degradación segura:
    error de infra → True (mejor mandar que tragar una orden real)."""
    try:
        _ensure()
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO operaciones.ordenes_idempotency (clave, ts, data) "
                "VALUES (%s, now(), %s) ON CONFLICT (clave) DO NOTHING",
                (key, Jsonb({"key": key, "status": "in_progress", "result": None})))
            reserved = cur.rowcount == 1
            conn.commit()
        return reserved
    except Exception as e:
        logger.warning("idempotencia: reservar falló (%s) — envío sin dedup", e)
        return True


def guardar_resultado(key: str, result: dict[str, Any]) -> None:
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE operaciones.ordenes_idempotency SET data = data || %s WHERE clave = %s",
                (Jsonb({"status": "done", "result": result,
                        "finished_at": datetime.now(UTC).isoformat()}), key))
            conn.commit()
    except Exception as e:
        logger.warning("idempotencia: guardar_resultado falló: %s", e)


def guardar_error(key: str, msg: str) -> None:
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE operaciones.ordenes_idempotency SET data = data || %s WHERE clave = %s",
                (Jsonb({"status": "error", "error": msg[:300],
                        "finished_at": datetime.now(UTC).isoformat()}), key))
            conn.commit()
    except Exception as e:
        logger.warning("idempotencia: guardar_error falló: %s", e)


def ejecutar_idempotente(key: str | None, fn):
    """Ejecuta `fn()` UNA sola vez por `key`. Reenvío con la misma clave →
    devuelve el resultado del primero sin re-ejecutar. `key` falsy → ejecuta
    directo (sin dedup), comportamiento idéntico al de siempre.

    Genérico: lo usan el envío directo, el dólar MEP y los brackets."""
    if not key:
        return fn()
    if not reservar(key):
        return esperar_resultado(key)
    try:
        result = fn()
    except Exception as e:
        guardar_error(key, str(e))
        raise
    guardar_resultado(key, result)
    return result


def esperar_resultado(key: str) -> dict[str, Any]:
    """Para una clave DUPLICADA: devuelve el resultado del 1er envío. Si el
    primero sigue en vuelo, espera hasta _WAIT_S (cubre el doble-click casi
    simultáneo → ambos terminan devolviendo el mismo resultado real)."""
    deadline = time.time() + _WAIT_S
    while time.time() < deadline:
        try:
            with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT data FROM operaciones.ordenes_idempotency WHERE clave = %s", (key,))
                r = cur.fetchone()
            doc = r["data"] if r else None
        except Exception:
            doc = None
        if doc:
            if doc.get("status") == "done" and doc.get("result") is not None:
                return doc["result"]
            if doc.get("status") == "error":
                return {
                    "ok": False, "cl_ord_id": None, "status": "DUPLICADA",
                    "error": "el envío previo con esta clave falló; usá una orden nueva para reintentar",
                    "duplicate": True,
                }
        time.sleep(_POLL_S)
    return {
        "ok": False, "cl_ord_id": None, "status": "DUPLICADA_EN_PROCESO",
        "error": "orden duplicada — el envío original sigue en proceso",
        "duplicate": True,
    }
