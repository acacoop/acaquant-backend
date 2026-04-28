"""Triggers condicionales sobre la operativa Dólar MEP.

El user define un MEP objetivo (ej $1.420). Mientras el MEP > objetivo, el
trigger está ACTIVE. Cuando MEP <= objetivo, el scanner dispara la operativa
estándar (BUY AL30 + SELL AL30D, con todo el guard que ya tenemos en
crear_operativa: factor 100, espera ER, etc.).

Persistencia: Operaciones.TriggersMep.
  {
    trigger_id, account, actor_email,
    monto_ars, comision_pct, rueda,
    tc_objetivo,             # disparar cuando MEP <= este valor
    estado,                  # ACTIVE | FIRING | EXECUTED | CANCELLED |
                             # CANCELLED_EOD | FAIL
    operativa_id,            # link a OperativasMep cuando dispara
    last_seen_mep,           # último MEP visto por el scanner (debug)
    created_at, updated_at,
    fired_at?, error?,
  }

Scanner: corre como background task asyncio en el lifespan de api.main.
Cada N segundos:
  1) evaluar_y_disparar_pendientes() — lee ACTIVE, dispara los que cumplen
  2) cancelar_pendientes_eod() — si hora UTC ≥ 19:50, cancela todo ACTIVE

Concurrencia: lock optimista — un update_one filtra por estado=ACTIVE y
pasa a FIRING. Solo el primer scan que llegue gana, los demás skipean.

Stale guard: si el último trade de AL30 o AL30D tiene > STALE_THRESHOLD_S
segundos, NO se dispara — el motor de market data probablemente está
desconectado y la cotización es vieja.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pymongo import ASCENDING

from api.services.operativa_mep import (
    RUEDAS_VALIDAS,
    crear_operativa,
    get_cotizaciones,
)
from core.mongo import get_mongo_client, get_mongo_client_read

logger = logging.getLogger("api.services.triggers_mep")

DB_OPS = "Operaciones"
COL_TRIGGERS = "TriggersMep"

# Si el último trade de AL30 o AL30D supera esto sin actualizarse, no
# disparamos — la cotización puede ser fantasma (motor caído).
STALE_THRESHOLD_S = 5.0

# 16:50 ART = 19:50 UTC. A esa hora cancelamos todo trigger que quede vivo
# para que no se disparen al cierre o al día siguiente (TIF de las órdenes
# es DAY; un trigger sin uso podría dispararse mañana en otro contexto).
EOD_HOUR_UTC = 19
EOD_MIN_UTC = 50


# ─────────────────────────────────────────────────────────────────────────────
# Schema / helpers
# ─────────────────────────────────────────────────────────────────────────────


def _ensure_indexes() -> None:
    db = get_mongo_client()[DB_OPS]
    db[COL_TRIGGERS].create_index([("trigger_id", ASCENDING)], unique=True)
    db[COL_TRIGGERS].create_index([("estado", ASCENDING)])
    db[COL_TRIGGERS].create_index([("created_at", ASCENDING)])


def _serialize(doc: dict | None) -> dict | None:
    if not doc:
        return None
    out = dict(doc)
    out.pop("_id", None)
    for k in ("created_at", "updated_at", "fired_at"):
        v = out.get(k)
        if isinstance(v, datetime):
            out[k] = v.astimezone(UTC).isoformat()
    return out


# ─────────────────────────────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────────────────────────────


def crear_trigger(
    *,
    monto_ars: float,
    comision_pct: float,
    rueda: str,
    tc_objetivo: float,
    account: str | None = None,
    actor_email: str | None = None,
) -> dict[str, Any]:
    if rueda not in RUEDAS_VALIDAS:
        raise ValueError(f"rueda inválida: {rueda!r}")
    if monto_ars <= 0:
        raise ValueError("monto_ars debe ser > 0")
    if comision_pct < 0 or comision_pct > 5:
        raise ValueError("comision_pct fuera de rango [0,5]")
    if tc_objetivo <= 0:
        raise ValueError("tc_objetivo debe ser > 0")

    _ensure_indexes()
    trigger_id = str(uuid4())
    now = datetime.now(UTC)
    doc = {
        "trigger_id":   trigger_id,
        "account":      account,
        "actor_email":  actor_email,
        "monto_ars":    monto_ars,
        "comision_pct": comision_pct,
        "rueda":        rueda,
        "tc_objetivo":  tc_objetivo,
        "estado":       "ACTIVE",
        "operativa_id": None,
        "last_seen_mep": None,
        "created_at":   now,
        "updated_at":   now,
    }
    get_mongo_client()[DB_OPS][COL_TRIGGERS].insert_one(doc)
    logger.info(
        "trigger creado %s (rueda=%s, tc<=%s, monto=%s, actor=%s)",
        trigger_id, rueda, tc_objetivo, monto_ars, actor_email,
    )
    return {"ok": True, "trigger_id": trigger_id, "estado": "ACTIVE"}


def cancelar_trigger(trigger_id: str, *, actor_email: str | None = None) -> dict[str, Any]:
    """Cancela un trigger ACTIVE. Si está en otro estado, no-op."""
    db = get_mongo_client()[DB_OPS]
    res = db[COL_TRIGGERS].update_one(
        {"trigger_id": trigger_id, "estado": "ACTIVE"},
        {"$set": {
            "estado": "CANCELLED",
            "cancelled_by": actor_email,
            "updated_at": datetime.now(UTC),
        }},
    )
    if res.matched_count == 0:
        return {"ok": False, "error": "trigger no existe o no está ACTIVE"}
    return {"ok": True, "trigger_id": trigger_id, "estado": "CANCELLED"}


def listar_triggers_dia(account: str | None = None) -> list[dict]:
    """Triggers creados hoy (UTC). Más recientes primero."""
    inicio = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    filtro: dict[str, Any] = {"created_at": {"$gte": inicio}}
    if account:
        filtro["account"] = account
    cursor = get_mongo_client_read()[DB_OPS][COL_TRIGGERS].find(filtro).sort("created_at", -1)
    return [_serialize(d) for d in cursor if d]


# ─────────────────────────────────────────────────────────────────────────────
# Scanner
# ─────────────────────────────────────────────────────────────────────────────


def _is_cot_fresh(cot: dict, now: datetime) -> bool:
    """True si AL30 y AL30D tienen trades recientes (< STALE_THRESHOLD_S)."""
    for key in ("al30", "al30d"):
        leg = cot.get(key)
        if not leg or not leg.get("ts"):
            return False
        try:
            ts = leg["ts"]
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            edad = (now - ts).total_seconds()
            if edad > STALE_THRESHOLD_S:
                return False
        except (ValueError, TypeError):
            return False
    return True


def evaluar_y_disparar_pendientes() -> int:
    """Evalúa todos los triggers ACTIVE; dispara los que cumplen tc_objetivo.
    Devuelve la cantidad de triggers disparados en este tick.

    Diseño:
      1) Una sola lectura de cotización por rueda (no por trigger).
      2) Lock optimista por trigger: ACTIVE → FIRING en update atómico.
      3) Si la cotización no está fresca, NO disparar — al próximo tick
         se reintenta con datos nuevos.
    """
    db = get_mongo_client()[DB_OPS]
    activos = list(db[COL_TRIGGERS].find({"estado": "ACTIVE"}))
    if not activos:
        return 0

    now = datetime.now(UTC)
    cot_cache: dict[str, dict] = {}

    disparados = 0
    for t in activos:
        rueda = t.get("rueda")
        if rueda not in cot_cache:
            try:
                cot_cache[rueda] = get_cotizaciones(rueda)
            except Exception as e:
                logger.warning("cotizacion fallida para rueda=%s: %s", rueda, e)
                continue

        cot = cot_cache[rueda]
        mep = cot.get("mep_implicito")

        # Update last_seen_mep (debugging — independiente de disparo).
        db[COL_TRIGGERS].update_one(
            {"trigger_id": t["trigger_id"]},
            {"$set": {"last_seen_mep": mep, "updated_at": now}},
        )

        if mep is None:
            continue

        if not _is_cot_fresh(cot, now):
            # Cotización stale — esperar al próximo tick.
            logger.debug("trigger %s: cotización stale, skip", t["trigger_id"])
            continue

        if mep > t["tc_objetivo"]:
            # Todavía no se cumple la condición.
            continue

        # Lock optimista: el primero que pase ACTIVE → FIRING gana.
        res = db[COL_TRIGGERS].update_one(
            {"trigger_id": t["trigger_id"], "estado": "ACTIVE"},
            {"$set": {
                "estado": "FIRING",
                "fired_at": now,
                "fired_at_mep": mep,
                "updated_at": now,
            }},
        )
        if res.matched_count == 0:
            continue  # otro tick lo agarró primero

        # Disparar.
        try:
            op_resp = crear_operativa(
                monto_ars=t["monto_ars"],
                comision_pct=t["comision_pct"],
                rueda=t["rueda"],
                account=t.get("account"),
                actor_email=t.get("actor_email"),
            )
            estado_final = "EXECUTED" if op_resp.get("ok") else "FAIL"
            db[COL_TRIGGERS].update_one(
                {"trigger_id": t["trigger_id"]},
                {"$set": {
                    "estado":       estado_final,
                    "operativa_id": op_resp.get("operativa_id"),
                    "error":        op_resp.get("error"),
                    "updated_at":   datetime.now(UTC),
                }},
            )
            disparados += 1
            logger.info(
                "trigger %s disparado: mep=%s <= %s, operativa=%s estado=%s",
                t["trigger_id"], mep, t["tc_objetivo"],
                op_resp.get("operativa_id"), estado_final,
            )
        except Exception as e:
            logger.exception("trigger %s falló al disparar", t["trigger_id"])
            db[COL_TRIGGERS].update_one(
                {"trigger_id": t["trigger_id"]},
                {"$set": {
                    "estado":     "FAIL",
                    "error":      str(e),
                    "updated_at": datetime.now(UTC),
                }},
            )

    return disparados


def cancelar_pendientes_eod() -> int:
    """Si la hora UTC actual cruzó EOD_HOUR_UTC:EOD_MIN_UTC, cancela todos
    los triggers ACTIVE. Idempotente — se llama en cada tick del scanner
    pero solo ejecuta el mass-update cuando hay ACTIVE y la hora es >=
    el corte.

    Devuelve cantidad cancelada.
    """
    now = datetime.now(UTC)
    if (now.hour, now.minute) < (EOD_HOUR_UTC, EOD_MIN_UTC):
        return 0
    db = get_mongo_client()[DB_OPS]
    res = db[COL_TRIGGERS].update_many(
        {"estado": "ACTIVE"},
        {"$set": {
            "estado":     "CANCELLED_EOD",
            "updated_at": now,
        }},
    )
    if res.modified_count > 0:
        logger.info("EOD: cancelados %d trigger(s) ACTIVE", res.modified_count)
    return res.modified_count


# ─────────────────────────────────────────────────────────────────────────────
# Loop asyncio (montado en api.main lifespan)
# ─────────────────────────────────────────────────────────────────────────────


async def scanner_loop(interval_s: float = 1.0) -> None:
    """Background task que evalúa los triggers ACTIVE cada interval_s.

    Corre dentro del lifespan de api.main. Cada tick:
      1) evaluar_y_disparar_pendientes (puede disparar crear_operativa,
         que es síncrono y tarda hasta ~3s por el guard de BUY → corre en
         to_thread para no bloquear el event loop).
      2) cancelar_pendientes_eod (idempotente; no-op antes de las 19:50 UTC).

    No tira excepciones — cualquier fallo se logguea y se sigue. Apagar
    con cancel() desde el lifespan.
    """
    logger.info("scanner_loop arrancado (interval=%ss, stale=%ss, eod=%02d:%02d UTC)",
                interval_s, STALE_THRESHOLD_S, EOD_HOUR_UTC, EOD_MIN_UTC)
    try:
        while True:
            try:
                await asyncio.to_thread(evaluar_y_disparar_pendientes)
                await asyncio.to_thread(cancelar_pendientes_eod)
            except Exception:
                logger.exception("scanner_loop tick falló (sigo corriendo)")
            await asyncio.sleep(interval_s)
    except asyncio.CancelledError:
        logger.info("scanner_loop detenido (cancel)")
        raise
