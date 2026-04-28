"""Triggers condicionales sobre la operativa Dólar MEP — bracket order.

Modos:
  - Compra simple: ACTIVE → (MEP <= tc_objetivo) → EXECUTED
  - Bracket TP/SL: ACTIVE → (MEP <= tc_objetivo) → WAITING_EXIT
                   WAITING_EXIT → (MEP >= tp o MEP <= sl) → EXITED

Persistencia: Operaciones.TriggersMep.
  {
    trigger_id, account, actor_email,
    monto_ars, comision_pct, rueda,
    tc_objetivo,             # entry: disparar compra cuando MEP <= esto
    tp_objetivo, sl_objetivo,# salida (opcionales). Si alguno está, modo
                             # bracket; si no, modo compra simple.
    estado,                  # ACTIVE | FIRING | EXECUTED |
                             # WAITING_EXIT | EXITING | EXITED | EXIT_FAIL |
                             # CANCELLED | CANCELLED_EOD | FAIL
    operativa_id,            # link a OperativasMep de la entry (compra)
    operativa_exit_id,       # link a OperativasMep de la salida (venta)
    nominales_entry,         # nominales que entraron en la compra
    last_seen_mep,           # último MEP visto por el scanner (debug)
    created_at, updated_at,
    fired_at?, exit_fired_at?, error?,
  }

Scanner: corre como background task asyncio en el lifespan de api.main.
Cada N segundos, en orden:
  1) evaluar_y_disparar_pendientes() — entries (ACTIVE) y salidas (WAITING_EXIT)
  2) cancelar_pendientes_eod() — si hora UTC ≥ 19:50, cancela todo ACTIVE
     y WAITING_EXIT. Las posiciones abiertas con WAITING_EXIT NO se cierran
     forzosamente — el user las maneja al día siguiente.

Concurrencia: lock optimista — ACTIVE → FIRING, WAITING_EXIT → EXITING.
Stale guard: ver STALE_THRESHOLD_S.
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
    crear_operativa_venta,
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
    tp_objetivo: float | None = None,
    sl_objetivo: float | None = None,
    account: str | None = None,
    actor_email: str | None = None,
) -> dict[str, Any]:
    """Crea trigger ACTIVE. Si tp_objetivo o sl_objetivo están definidos,
    el trigger queda en modo bracket: tras la entry pasa a WAITING_EXIT
    y el scanner monitorea TP/SL para disparar la venta inversa."""
    if rueda not in RUEDAS_VALIDAS:
        raise ValueError(f"rueda inválida: {rueda!r}")
    if monto_ars <= 0:
        raise ValueError("monto_ars debe ser > 0")
    if comision_pct < 0 or comision_pct > 5:
        raise ValueError("comision_pct fuera de rango [0,5]")
    if tc_objetivo <= 0:
        raise ValueError("tc_objetivo debe ser > 0")
    if tp_objetivo is not None and tp_objetivo <= 0:
        raise ValueError("tp_objetivo debe ser > 0")
    if sl_objetivo is not None and sl_objetivo <= 0:
        raise ValueError("sl_objetivo debe ser > 0")

    _ensure_indexes()
    trigger_id = str(uuid4())
    now = datetime.now(UTC)
    doc = {
        "trigger_id":        trigger_id,
        "account":           account,
        "actor_email":       actor_email,
        "monto_ars":         monto_ars,
        "comision_pct":      comision_pct,
        "rueda":             rueda,
        "tc_objetivo":       tc_objetivo,
        "tp_objetivo":       tp_objetivo,
        "sl_objetivo":       sl_objetivo,
        "estado":            "ACTIVE",
        "operativa_id":      None,
        "operativa_exit_id": None,
        "nominales_entry":   None,
        "last_seen_mep":     None,
        "created_at":        now,
        "updated_at":        now,
    }
    get_mongo_client()[DB_OPS][COL_TRIGGERS].insert_one(doc)
    logger.info(
        "trigger creado %s (rueda=%s, tc<=%s, tp>=%s, sl<=%s, monto=%s, actor=%s)",
        trigger_id, rueda, tc_objetivo, tp_objetivo, sl_objetivo, monto_ars, actor_email,
    )
    return {"ok": True, "trigger_id": trigger_id, "estado": "ACTIVE"}


def cancelar_trigger(trigger_id: str, *, actor_email: str | None = None) -> dict[str, Any]:
    """Cancela un trigger en estado ACTIVE o WAITING_EXIT.

    En ACTIVE → CANCELLED (la compra nunca se ejecuta).
    En WAITING_EXIT → CANCELLED (la compra YA se ejecutó; la posición USD
    queda abierta. El user la cierra manualmente cuando quiera.).
    En cualquier otro estado → no-op.
    """
    db = get_mongo_client()[DB_OPS]
    res = db[COL_TRIGGERS].update_one(
        {"trigger_id": trigger_id, "estado": {"$in": ["ACTIVE", "WAITING_EXIT"]}},
        {"$set": {
            "estado": "CANCELLED",
            "cancelled_by": actor_email,
            "updated_at": datetime.now(UTC),
        }},
    )
    if res.matched_count == 0:
        return {"ok": False, "error": "trigger no existe o no es cancelable"}
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
    """Evalúa triggers en estado ACTIVE (entry) y WAITING_EXIT (salida).
    Devuelve la cantidad de triggers que disparó alguna acción este tick.

    Diseño:
      - Una sola lectura de cotización por rueda (no por trigger).
      - Lock optimista por trigger: ACTIVE → FIRING o WAITING_EXIT → EXITING.
      - Stale guard: NO dispara si la cotización está vieja (>5s).
    """
    db = get_mongo_client()[DB_OPS]
    activos = list(
        db[COL_TRIGGERS].find({"estado": {"$in": ["ACTIVE", "WAITING_EXIT"]}})
    )
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

        # Tick para debugging — independiente de si dispara o no.
        db[COL_TRIGGERS].update_one(
            {"trigger_id": t["trigger_id"]},
            {"$set": {"last_seen_mep": mep, "updated_at": now}},
        )

        if mep is None or not _is_cot_fresh(cot, now):
            continue

        estado = t.get("estado")
        if estado == "ACTIVE":
            if _disparar_entry(db, t, mep, now):
                disparados += 1
        elif estado == "WAITING_EXIT":
            if _disparar_exit(db, t, mep, now):
                disparados += 1

    return disparados


def _disparar_entry(db, t: dict, mep: float, now: datetime) -> bool:
    """Si MEP <= tc_objetivo, ejecuta la compra. Si el trigger tiene TP
    o SL, queda en WAITING_EXIT; sino EXECUTED. Devuelve True si actuó."""
    if mep > t["tc_objetivo"]:
        return False

    res = db[COL_TRIGGERS].update_one(
        {"trigger_id": t["trigger_id"], "estado": "ACTIVE"},
        {"$set": {
            "estado":       "FIRING",
            "fired_at":     now,
            "fired_at_mep": mep,
            "updated_at":   now,
        }},
    )
    if res.matched_count == 0:
        return False  # otro thread lo agarró

    try:
        op_resp = crear_operativa(
            monto_ars=t["monto_ars"],
            comision_pct=t["comision_pct"],
            rueda=t["rueda"],
            account=t.get("account"),
            actor_email=t.get("actor_email"),
        )
        # Si la compra falla → FAIL, terminó.
        # Si la compra OK y NO hay TP/SL → EXECUTED, terminó.
        # Si la compra OK y hay TP o SL → WAITING_EXIT, esperamos salida.
        nominales_entry = op_resp.get("nominales") or 0
        op_ok = op_resp.get("ok") and nominales_entry > 0
        tiene_bracket = (t.get("tp_objetivo") is not None
                         or t.get("sl_objetivo") is not None)

        if not op_ok:
            estado_final = "FAIL"
        elif tiene_bracket:
            estado_final = "WAITING_EXIT"
        else:
            estado_final = "EXECUTED"

        db[COL_TRIGGERS].update_one(
            {"trigger_id": t["trigger_id"]},
            {"$set": {
                "estado":          estado_final,
                "operativa_id":    op_resp.get("operativa_id"),
                "nominales_entry": nominales_entry,
                "error":           op_resp.get("error"),
                "updated_at":      datetime.now(UTC),
            }},
        )
        logger.info(
            "trigger %s entry: mep=%s <= %s, operativa=%s nominales=%s estado=%s",
            t["trigger_id"], mep, t["tc_objetivo"],
            op_resp.get("operativa_id"), nominales_entry, estado_final,
        )
        return True
    except Exception as e:
        logger.exception("trigger %s falló al disparar entry", t["trigger_id"])
        db[COL_TRIGGERS].update_one(
            {"trigger_id": t["trigger_id"]},
            {"$set": {"estado": "FAIL", "error": str(e), "updated_at": datetime.now(UTC)}},
        )
        return True


def _disparar_exit(db, t: dict, mep: float, now: datetime) -> bool:
    """Si MEP cruza TP (>= tp_objetivo) o SL (<= sl_objetivo), ejecuta venta
    inversa de los nominales que entraron. Devuelve True si actuó."""
    tp = t.get("tp_objetivo")
    sl = t.get("sl_objetivo")
    motivo: str | None = None
    if tp is not None and mep >= tp:
        motivo = "TP"
    elif sl is not None and mep <= sl:
        motivo = "SL"
    if motivo is None:
        return False

    nominales = t.get("nominales_entry") or 0
    if nominales <= 0:
        # Defensivo — no debería pasar, pero si pasa marcamos error y salimos.
        db[COL_TRIGGERS].update_one(
            {"trigger_id": t["trigger_id"]},
            {"$set": {
                "estado":     "EXIT_FAIL",
                "error":      "nominales_entry <= 0 — no podemos cerrar la posición",
                "updated_at": datetime.now(UTC),
            }},
        )
        return True

    res = db[COL_TRIGGERS].update_one(
        {"trigger_id": t["trigger_id"], "estado": "WAITING_EXIT"},
        {"$set": {
            "estado":         "EXITING",
            "exit_fired_at":  now,
            "exit_fired_mep": mep,
            "exit_motivo":    motivo,
            "updated_at":     now,
        }},
    )
    if res.matched_count == 0:
        return False

    try:
        venta_resp = crear_operativa_venta(
            nominales=nominales,
            rueda=t["rueda"],
            account=t.get("account"),
            actor_email=t.get("actor_email"),
            parent_trigger_id=t["trigger_id"],
        )
        estado_final = "EXITED" if venta_resp.get("ok") else "EXIT_FAIL"
        db[COL_TRIGGERS].update_one(
            {"trigger_id": t["trigger_id"]},
            {"$set": {
                "estado":            estado_final,
                "operativa_exit_id": venta_resp.get("operativa_id"),
                "exit_error":        venta_resp.get("error"),
                "updated_at":        datetime.now(UTC),
            }},
        )
        logger.info(
            "trigger %s exit (%s): mep=%s, operativa_venta=%s estado=%s",
            t["trigger_id"], motivo, mep,
            venta_resp.get("operativa_id"), estado_final,
        )
        return True
    except Exception as e:
        logger.exception("trigger %s falló al disparar exit", t["trigger_id"])
        db[COL_TRIGGERS].update_one(
            {"trigger_id": t["trigger_id"]},
            {"$set": {
                "estado":     "EXIT_FAIL",
                "exit_error": str(e),
                "updated_at": datetime.now(UTC),
            }},
        )
        return True


def cancelar_pendientes_eod() -> int:
    """Cuando hora UTC ≥ EOD_HOUR_UTC:EOD_MIN_UTC, cancela todos los
    triggers en ACTIVE y WAITING_EXIT. Idempotente.

    Importante: para WAITING_EXIT NO se fuerza la venta — la posición USD
    queda abierta. El user la cierra manualmente al día siguiente. Eso
    es lo acordado: 16:50 ART cancela el trigger, no el riesgo.

    Devuelve cantidad cancelada.
    """
    now = datetime.now(UTC)
    if (now.hour, now.minute) < (EOD_HOUR_UTC, EOD_MIN_UTC):
        return 0
    db = get_mongo_client()[DB_OPS]
    res = db[COL_TRIGGERS].update_many(
        {"estado": {"$in": ["ACTIVE", "WAITING_EXIT"]}},
        {"$set": {"estado": "CANCELLED_EOD", "updated_at": now}},
    )
    if res.modified_count > 0:
        logger.info("EOD: cancelados %d trigger(s) en ACTIVE/WAITING_EXIT", res.modified_count)
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
