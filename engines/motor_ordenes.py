"""Motor de órdenes — escucha execution reports y persiste el ciclo de vida.

Es proceso aparte (systemd unit propio). El API tiene su propia sesión
pyRofex liviana para REST `send_order`/`cancel_order`. Acá vivimos el
WS con `order_report_subscription` y persistimos cada ER que llega del
broker, así el frontend lee el estado actualizado de Mongo (no espera
respuesta sincrónica del broker).

Flujo:
  1. Inicializar sesión pyRofex con WS + handler de order_report.
  2. Recovery on start: leer `Operaciones.OrdenesLive` con estado != FINAL,
     consultar `pyRofex.get_all_orders_status()` y reconciliar (cierra
     órdenes que ya completaron mientras el motor estaba caído, marca
     huérfanas las que el broker no conoce).
  3. Loop principal: el handler corre en thread del WS, este loop solo
     hace heartbeat y maneja cierre limpio.

Colecciones:
  Operaciones.OrdenesLive:
    {
      _id, cl_ord_id, ws_cl_ord_id, account, ticker, side, order_type,
      tif, size, price, status,
      cum_qty, leaves_qty, avg_px, last_px, last_qty,
      reject_reason, actor_email,
      created_at, updated_at, last_er_ts
    }
    upsert por cl_ord_id (last write wins).

  Operaciones.OrdenesAudit:
    {
      _id, ts, kind, cl_ord_id, ws_cl_ord_id, account, actor_email,
      payload  # ER crudo o dict del request
    }
    append-only. `kind` ∈ SEND_REQUEST | SEND_ERROR | CANCEL_REQUEST |
    CANCEL_ERROR | EXECUTION_REPORT | RECOVERY.

Ejecutar:
    python -m engines.motor_ordenes
"""
from __future__ import annotations

import logging
import os
import signal
import threading
import time
from datetime import UTC, datetime
from typing import Any

import pyRofex
from dotenv import load_dotenv
from pymongo import ASCENDING

# Cargar .env antes que core/rofex_orders_session lea ROFEX_ORDERS_ENV y demás.
load_dotenv()

from core.mongo import get_mongo_client  # noqa: E402
from core.rofex_orders_session import (  # noqa: E402
    cerrar_ws,
    inicializar_para_motor,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("MotorOrdenes")

DB_NAME = "Operaciones"
COL_LIVE = "OrdenesLive"
COL_AUDIT = "OrdenesAudit"
COL_HEARTBEAT = "MotorOrdenesHeartbeat"

# Estados que consideramos terminales — no hace falta re-fetchearlos.
ESTADOS_FINALES = {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}

# Heartbeat del loop principal (no afecta latencia de ER).
HEARTBEAT_S = 5
# Escritura de heartbeat para que /manager → DIAG vea que el motor está vivo.
# Sin esto, motor_ordenes no figura porque solo escribe cuando llega un ER
# (puede pasar horas sin actividad).
HEARTBEAT_DB_S = 30

_running = True
_lock = threading.Lock()


def _handle_signal(sig, frame):
    global _running
    logger.info("Señal de cierre recibida, apagando motor de órdenes.")
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ─────────────────────────────────────────────────────────────────────────────
# Persistencia
# ─────────────────────────────────────────────────────────────────────────────


def _ensure_indexes(db) -> None:
    """Idempotente — pymongo no recrea índices ya existentes."""
    db[COL_LIVE].create_index([("cl_ord_id", ASCENDING)], unique=True, sparse=True)
    db[COL_LIVE].create_index([("ws_cl_ord_id", ASCENDING)], sparse=True)
    db[COL_LIVE].create_index([("account", ASCENDING), ("status", ASCENDING)])
    db[COL_LIVE].create_index([("updated_at", ASCENDING)])
    db[COL_AUDIT].create_index([("ts", ASCENDING)])
    db[COL_AUDIT].create_index([("cl_ord_id", ASCENDING)])


def _audit(db, kind: str, *, cl_ord_id: str | None = None,
           ws_cl_ord_id: str | None = None, account: str | None = None,
           actor_email: str | None = None, payload: dict | None = None) -> None:
    db[COL_AUDIT].insert_one({
        "ts": datetime.now(UTC),
        "kind": kind,
        "cl_ord_id": cl_ord_id,
        "ws_cl_ord_id": ws_cl_ord_id,
        "account": account,
        "actor_email": actor_email,
        "payload": payload or {},
    })


def _upsert_live_from_er(db, rep: dict[str, Any]) -> None:
    """Mapea un orderReport del broker a la forma normalizada y hace upsert."""
    cl_ord_id = rep.get("clOrdId")
    if not cl_ord_id:
        # Sin clOrdId no podemos identificar la orden — al audit y listo.
        return

    instrument = rep.get("instrumentId") or {}
    ticker = instrument.get("symbol") or rep.get("symbol") or ""

    now = datetime.now(UTC)
    doc_set = {
        "ws_cl_ord_id": rep.get("wsClOrdId"),
        "account": (rep.get("accountId") or {}).get("id") if isinstance(rep.get("accountId"), dict) else rep.get("accountId"),
        "ticker": ticker,
        "side": rep.get("side"),
        "order_type": rep.get("ordType"),
        "tif": rep.get("timeInForce"),
        "size": rep.get("orderQty"),
        "price": rep.get("price"),
        "status": rep.get("status"),
        "cum_qty": rep.get("cumQty"),
        "leaves_qty": rep.get("leavesQty"),
        "avg_px": rep.get("avgPx"),
        "last_px": rep.get("lastPx"),
        "last_qty": rep.get("lastQty"),
        "reject_reason": rep.get("text") if rep.get("status") in ("REJECTED", "CANCELLED") else None,
        "updated_at": now,
        "last_er_ts": rep.get("transactTime") or now,
    }
    # `created_at` solo en insert (no se pisa).
    db[COL_LIVE].update_one(
        {"cl_ord_id": cl_ord_id},
        {"$set": {k: v for k, v in doc_set.items() if v is not None},
         "$setOnInsert": {"cl_ord_id": cl_ord_id, "created_at": now}},
        upsert=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Handler de execution reports (corre en thread del WS)
# ─────────────────────────────────────────────────────────────────────────────


def _make_er_handler(db):
    def _handler(message: dict) -> None:
        try:
            rep = message.get("orderReport") or {}
            if not rep:
                return
            with _lock:
                _upsert_live_from_er(db, rep)
                _audit(
                    db, "EXECUTION_REPORT",
                    cl_ord_id=rep.get("clOrdId"),
                    ws_cl_ord_id=rep.get("wsClOrdId"),
                    payload=message,
                )
            logger.info(
                "ER %s %s %s status=%s lastPx=%s lastQty=%s",
                rep.get("clOrdId"),
                rep.get("side"),
                (rep.get("instrumentId") or {}).get("symbol"),
                rep.get("status"),
                rep.get("lastPx"),
                rep.get("lastQty"),
            )
        except Exception as e:
            logger.error("Error procesando ER: %s", e, exc_info=True)
    return _handler


# ─────────────────────────────────────────────────────────────────────────────
# Recovery on start
# ─────────────────────────────────────────────────────────────────────────────


def _recovery(db, _account_master: str) -> None:
    """Reconcilia OrdenesLive contra el broker, agrupando por cuenta REAL
    de la orden (no la del master).

    BUG histórico: antes filtraba `{"account": account_master}` y pedía
    `get_all_orders_status(account=master)`. Pero el master nunca opera
    — opera con cuentas 100/255/805/etc. autorizadas. Resultado: las
    órdenes en esas cuentas quedaban PENDING_NEW para siempre porque
    el recovery no las miraba.

    Ahora:
      1. Lee TODAS las órdenes locales en estado no-final (sin filtrar
         por cuenta).
      2. Agrupa por `account`.
      3. Para cada cuenta, pega `get_all_orders_status(account=X)` y
         reconcilia las que matchean por clOrdId.
      4. Las locales que el broker no conoce → UNKNOWN_LOCAL.
    """
    pendientes = list(db[COL_LIVE].find(
        {"status": {"$nin": list(ESTADOS_FINALES) + [None]}},
        {"cl_ord_id": 1, "account": 1},
    ))
    if not pendientes:
        logger.info("Recovery: sin órdenes pendientes locales — nada que reconciliar.")
        return

    # Agrupar por cuenta. Las que no tienen `account` (caso raro) van a un
    # bucket especial que igual intentamos contra el master por compat.
    por_cuenta: dict[str, set[str]] = {}
    for p in pendientes:
        cid = p.get("cl_ord_id")
        if not cid:
            continue
        acc = str(p.get("account") or _account_master)
        por_cuenta.setdefault(acc, set()).add(cid)

    total = sum(len(v) for v in por_cuenta.values())
    logger.info(
        "Recovery: %d orden(es) pendientes en %d cuenta(s): %s",
        total, len(por_cuenta), list(por_cuenta.keys()),
    )

    vistos: set[str] = set()
    for acc, cl_ord_locales in por_cuenta.items():
        try:
            resp = pyRofex.get_all_orders_status(account=acc)
        except Exception as e:
            logger.error("Recovery acc=%s: get_all_orders_status falló: %s", acc, e)
            continue

        if not resp or resp.get("status") != "OK":
            logger.warning("Recovery acc=%s: respuesta no-OK del broker: %s", acc, resp)
            continue

        for o in resp.get("orders", []):
            rep = o.get("orderReport", o)
            cid = rep.get("clOrdId", "")
            if cid in cl_ord_locales:
                with _lock:
                    _upsert_live_from_er(db, rep)
                    _audit(db, "RECOVERY", cl_ord_id=cid, account=acc, payload=rep)
                vistos.add(cid)

    todas_locales = set().union(*por_cuenta.values()) if por_cuenta else set()
    huerfanas = todas_locales - vistos
    if huerfanas:
        logger.warning(
            "Recovery: %d orden(es) local(es) que el broker no conoce — marcando UNKNOWN_LOCAL: %s",
            len(huerfanas), list(huerfanas)[:5],
        )
        now = datetime.now(UTC)
        db[COL_LIVE].update_many(
            {"cl_ord_id": {"$in": list(huerfanas)}},
            {"$set": {"status": "UNKNOWN_LOCAL", "updated_at": now}},
        )
        for cid in huerfanas:
            _audit(db, "RECOVERY", cl_ord_id=cid,
                   payload={"reason": "no encontrada en broker"})

    logger.info("Recovery: reconciliadas=%d, huérfanas=%d", len(vistos), len(huerfanas))


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────


def _heartbeat_loop(db, account: str) -> None:
    """Thread daemon: cada HEARTBEAT_DB_S segundos escribe Operaciones.
    MotorOrdenesHeartbeat para que /manager → DIAG sepa que el motor
    está vivo. A diferencia de los otros motores, este no escribe
    snapshots periódicos (solo ERs reactivos), por eso necesita el
    heartbeat explícito."""
    while _running:
        try:
            db[COL_HEARTBEAT].update_one(
                {"_id": "singleton"},
                {"$set": {
                    "updated_at": datetime.now(UTC),
                    "account":    account,
                }},
                upsert=True,
            )
        except Exception as e:
            logger.warning("heartbeat write falló: %s", e)
        time.sleep(HEARTBEAT_DB_S)


def main() -> None:
    db = get_mongo_client()[DB_NAME]
    _ensure_indexes(db)

    handler = _make_er_handler(db)
    account, env = inicializar_para_motor(handler)
    logger.info("Motor de órdenes ARRIBA (cuenta=%s, env=%s)", account, env.name)

    # El motor escucha solo la cuenta master para audit general. Las
    # operativas de mesa (operativa_mep) ya no dependen del WS — confirman
    # los fills vía REST `pyRofex.get_order_status` directo al broker.
    _recovery(db, account)

    # Heartbeat para monitoreo desde /manager → DIAG.
    threading.Thread(
        target=_heartbeat_loop,
        args=(db, account),
        daemon=True,
    ).start()

    while _running:
        time.sleep(HEARTBEAT_S)

    logger.info("Apagando motor de órdenes…")
    cerrar_ws()
    logger.info("Motor de órdenes detenido.")


if __name__ == "__main__":
    # Redirigir el log a archivo si se ejecuta vía systemd (stdout va al journal).
    log_path = os.getenv("MOTOR_ORDENES_LOG")
    if log_path:
        fh = logging.FileHandler(log_path)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(fh)
    main()
