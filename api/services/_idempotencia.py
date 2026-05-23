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

from pymongo.errors import DuplicateKeyError

from core.mongo import get_mongo_client

logger = logging.getLogger(__name__)

_DB = "Operaciones"
_COL = "OrdenesIdempotency"
_TTL_S = 86_400      # 1 día
_WAIT_S = 4.0        # espera máx. del resultado del 1er envío en una dup concurrente
_POLL_S = 0.1

_idx_ready = False


def _col():
    global _idx_ready
    c = get_mongo_client()[_DB][_COL]
    if not _idx_ready:
        try:
            c.create_index("key", unique=True)
            c.create_index("created_at", expireAfterSeconds=_TTL_S)
        except Exception as e:
            logger.warning("idempotencia: no pude crear índices: %s", e)
        _idx_ready = True
    return c


def reservar(key: str) -> bool:
    """True si reservamos la clave (somos el 1er envío → hay que mandar).
    False si ya existía (es un duplicado → NO mandar, ver esperar_resultado)."""
    try:
        _col().insert_one(
            {"key": key, "status": "in_progress", "result": None, "created_at": datetime.now(UTC)}
        )
        return True
    except DuplicateKeyError:
        return False
    except Exception as e:
        # Infra caída → degradamos a "sin dedup": mejor mandar que tragar.
        logger.warning("idempotencia: reservar falló (%s) — envío sin dedup", e)
        return True


def guardar_resultado(key: str, result: dict[str, Any]) -> None:
    try:
        _col().update_one(
            {"key": key},
            {"$set": {"status": "done", "result": result, "finished_at": datetime.now(UTC)}},
        )
    except Exception as e:
        logger.warning("idempotencia: guardar_resultado falló: %s", e)


def guardar_error(key: str, msg: str) -> None:
    try:
        _col().update_one(
            {"key": key},
            {"$set": {"status": "error", "error": msg[:300], "finished_at": datetime.now(UTC)}},
        )
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
            doc = _col().find_one({"key": key})
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
