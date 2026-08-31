"""Motor de órdenes — escucha execution reports y persiste el ciclo de vida.

Es proceso aparte (systemd unit propio). El API tiene su propia sesión
pyRofex liviana para REST `send_order`/`cancel_order`. Acá vivimos el
WS con `order_report_subscription` y persistimos cada ER que llega del
broker, así el frontend lee el estado actualizado de la base (no espera
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

# Cargar .env antes que core/rofex_orders_session lea ROFEX_ORDERS_ENV y demás.
load_dotenv()

from core.logs import configurar  # noqa: E402
from core.rofex_orders_session import (  # noqa: E402
    cerrar_ws,
    inicializar_para_motor,
)

# El formato (con NIVEL) vive en core/logs — ver `AGENT.md` §0.ac.
configurar()
logger = logging.getLogger("MotorOrdenes")

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


def _audit(kind: str, *, cl_ord_id: str | None = None,
           ws_cl_ord_id: str | None = None, account: str | None = None,
           actor_email: str | None = None, payload: dict | None = None) -> None:
    ts = datetime.now(UTC)
    # SQL-native (decomiso 2026-06-29): append a operaciones.ordenes_audit, sin Mongo.
    from core import pg_mirror
    pg_mirror.append_native("operaciones.ordenes_audit", [{
        "ts": ts, "kind": kind, "cl_ord_id": cl_ord_id, "account": account,
        "actor_email": actor_email,
        "data": pg_mirror.doc_iso({"ws_cl_ord_id": ws_cl_ord_id, "payload": payload or {}}),
    }])


def _upsert_live_from_er(rep: dict[str, Any]) -> None:
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
    # SQL-native (decomiso 2026-06-29): read-modify-write a operaciones.ordenes_live. El
    # estado de la orden es INCREMENTAL (cada ER pisa solo sus campos no-null) → leemos el
    # doc actual, mergeamos y reescribimos, preservando `created_at` (como el $setOnInsert
    # de Mongo). Corre bajo `_lock` (single-thread) → no hay race read→write. `created_at`
    # vive en el jsonb (el read-side filtra /dia por data->>'created_at').
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    from core import pg_mirror
    from core.postgres import get_pool
    nuevos = {k: v for k, v in doc_set.items() if v is not None}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT data FROM operaciones.ordenes_live WHERE cl_ord_id = %s", (cl_ord_id,))
        row = cur.fetchone()
        prev = (row["data"] if row else None) or {}
        merged = {**prev, **nuevos, "cl_ord_id": cl_ord_id}
        merged.setdefault("created_at", now.isoformat())  # solo en el 1er insert
        cur.execute(
            "INSERT INTO operaciones.ordenes_live (cl_ord_id, account, ticker, estado, updated_at, data) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (cl_ord_id) DO UPDATE SET "
            "account = EXCLUDED.account, ticker = EXCLUDED.ticker, estado = EXCLUDED.estado, "
            "updated_at = EXCLUDED.updated_at, data = EXCLUDED.data",
            (cl_ord_id, merged.get("account"), merged.get("ticker"), merged.get("status"),
             now, Jsonb(pg_mirror.doc_iso(merged))))
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Handler de execution reports (corre en thread del WS)
# ─────────────────────────────────────────────────────────────────────────────


def _make_er_handler():
    def _handler(message: dict) -> None:
        try:
            rep = message.get("orderReport") or {}
            if not rep:
                return
            with _lock:
                _upsert_live_from_er(rep)
                _audit(
                    "EXECUTION_REPORT",
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

            # Hook de brackets: si esta orden es la entrada de un bracket
            # PENDING_ENTRY y llegó a FILLED, disparamos la salida.
            _maybe_dispatch_bracket_exit(rep)
        except Exception as e:
            logger.error("Error procesando ER: %s", e, exc_info=True)
    return _handler


def _maybe_dispatch_bracket_exit(rep: dict[str, Any]) -> None:
    """Si el ER recibido corresponde a la entrada de un bracket en PENDING_ENTRY,
    actúa según el status:

      - FILLED              → manda la salida LIMIT (side opuesto al de la entrada)
                              y marca el bracket EXIT_SENT.
      - REJECTED/CANCELLED/EXPIRED → marca ENTRY_CANCELLED, no hay salida.
      - otros               → no-op, esperamos el próximo ER.

    También maneja el lado de la salida: si llega FILLED para el exit_cl_ord_id
    del bracket, marcamos COMPLETED.
    """
    from core import brackets

    cl_ord_id = rep.get("clOrdId")
    status = rep.get("status")
    if not cl_ord_id or not status:
        return

    # Caso 1: esto es la SALIDA de un bracket → solo actualizamos estado final.
    try:
        bracket_exit = brackets.find_by_exit(cl_ord_id)
    except Exception as e:
        logger.warning("brackets.find_by_exit falló: %s", e)
        bracket_exit = None
    if bracket_exit:
        if status == "FILLED":
            brackets.mark_completed(cl_ord_id)
            logger.info(
                "Bracket %s COMPLETADO (salida %s FILLED)",
                bracket_exit.get("entry_cl_ord_id"), cl_ord_id,
            )
        elif status in brackets.ENTRY_DEAD:
            brackets.mark_exit_rejected(
                bracket_exit.get("entry_cl_ord_id", ""),
                rep.get("text") or status,
            )
            logger.warning(
                "Bracket %s SALIDA %s terminó %s — intervención manual",
                bracket_exit.get("entry_cl_ord_id"), cl_ord_id, status,
            )
        return

    # Caso 2: esto es la ENTRADA de un bracket pendiente.
    try:
        bracket = brackets.find_pending_by_entry(cl_ord_id)
    except Exception as e:
        logger.warning("brackets.find_pending_by_entry falló: %s", e)
        return
    if not bracket:
        return

    if status in brackets.ENTRY_DEAD:
        brackets.mark_entry_dead(cl_ord_id, status)
        logger.info(
            "Bracket %s ENTRY %s terminó %s — no se manda salida",
            cl_ord_id, bracket.get("ticker"), status,
        )
        return

    if status not in brackets.ENTRY_FILLED:
        return  # PARTIALLY_FILLED, NEW, PENDING_NEW, etc. — esperamos más.

    # ── FILLED: disparar la salida ──
    side_exit = "SELL" if bracket["side_entry"] == "BUY" else "BUY"
    logger.info(
        "Bracket %s entrada FILLED — disparando salida %s LIMIT %s x %s @ %s",
        cl_ord_id, side_exit, bracket["ticker"], bracket["size"], bracket["price_exit"],
    )

    try:
        exit_resp = pyRofex.send_order(
            ticker=bracket["ticker"],
            side=pyRofex.Side.SELL if side_exit == "SELL" else pyRofex.Side.BUY,
            size=int(bracket["size"]),
            price=float(bracket["price_exit"]),
            order_type=pyRofex.OrderType.LIMIT,
            time_in_force=getattr(
                pyRofex.TimeInForce, bracket.get("tif", "DAY"), pyRofex.TimeInForce.DAY,
            ),
            account=bracket["account"],
            cancel_previous=False,
        )
    except Exception as e:
        logger.error("Bracket %s falló al enviar salida: %s", cl_ord_id, e, exc_info=True)
        brackets.mark_exit_rejected(cl_ord_id, str(e))
        return

    if not exit_resp or exit_resp.get("status") != "OK":
        reason = (exit_resp or {}).get("description", "broker rechazó la salida")
        logger.error("Bracket %s salida rechazada: %s", cl_ord_id, reason)
        brackets.mark_exit_rejected(cl_ord_id, reason)
        return

    order_blk = exit_resp.get("order") or {}
    exit_cl_ord_id = order_blk.get("clientId") or order_blk.get("clOrdId")
    exit_proprietary = order_blk.get("proprietary")
    if not exit_cl_ord_id:
        brackets.mark_exit_rejected(cl_ord_id, "broker OK pero sin clientId")
        return

    brackets.mark_exit_sent(
        cl_ord_id,
        exit_cl_ord_id=exit_cl_ord_id,
        exit_proprietary=exit_proprietary,
    )
    _audit(
        "BRACKET_EXIT_SENT",
        cl_ord_id=exit_cl_ord_id,
        account=bracket["account"],
        actor_email=bracket.get("actor_email"),
        payload={
            "entry_cl_ord_id": cl_ord_id,
            "exit_cl_ord_id":  exit_cl_ord_id,
            "side_exit":       side_exit,
            "price_exit":      bracket["price_exit"],
            "size":            bracket["size"],
        },
    )
    logger.info(
        "Bracket %s salida ENVIADA (exit_cl_ord_id=%s)",
        cl_ord_id, exit_cl_ord_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Recovery on start
# ─────────────────────────────────────────────────────────────────────────────


def _recovery(_account_master: str) -> None:
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
    # SQL-native (decomiso 2026-06-29): órdenes locales no-finales desde operaciones.ordenes_live.
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT cl_ord_id, account FROM operaciones.ordenes_live "
                    "WHERE estado IS NOT NULL AND NOT (estado = ANY(%s))",
                    (list(ESTADOS_FINALES),))
        pendientes = [{"cl_ord_id": r[0], "account": r[1]} for r in cur.fetchall()]
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
                    _upsert_live_from_er(rep)
                    _audit("RECOVERY", cl_ord_id=cid, account=acc, payload=rep)
                vistos.add(cid)

    todas_locales = set().union(*por_cuenta.values()) if por_cuenta else set()
    huerfanas = todas_locales - vistos
    if huerfanas:
        logger.warning(
            "Recovery: %d orden(es) local(es) que el broker no conoce — marcando UNKNOWN_LOCAL: %s",
            len(huerfanas), list(huerfanas)[:5],
        )
        now = datetime.now(UTC)
        # SQL-native: marca UNKNOWN_LOCAL en operaciones.ordenes_live (merge jsonb, sin Mongo).
        from psycopg.types.json import Jsonb

        from core.postgres import get_pool
        patch = Jsonb({"status": "UNKNOWN_LOCAL", "updated_at": now.isoformat()})
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE operaciones.ordenes_live SET estado = 'UNKNOWN_LOCAL', updated_at = %s, "
                "data = data || %s WHERE cl_ord_id = ANY(%s)",
                (now, patch, list(huerfanas)))
            conn.commit()
        for cid in huerfanas:
            _audit("RECOVERY", cl_ord_id=cid,
                   payload={"reason": "no encontrada en broker"})

    logger.info("Recovery: reconciliadas=%d, huérfanas=%d", len(vistos), len(huerfanas))


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────


def _heartbeat_loop(account: str) -> None:
    """Thread daemon: cada HEARTBEAT_DB_S segundos escribe Operaciones.
    MotorOrdenesHeartbeat para que /manager → DIAG sepa que el motor
    está vivo. A diferencia de los otros motores, este no escribe
    snapshots periódicos (solo ERs reactivos), por eso necesita el
    heartbeat explícito."""
    while _running:
        try:
            ts = datetime.now(UTC)
            # SQL-native (decomiso 2026-06-29): heartbeat a operaciones.motor_heartbeat, sin Mongo.
            from core import pg_mirror
            pg_mirror.write_native("operaciones.motor_heartbeat", ["id"], [{
                "id": "current", "updated_at": ts,
                "data": pg_mirror.doc_iso({"account": account})}])
        except Exception as e:
            logger.warning("heartbeat write falló: %s", e)
        time.sleep(HEARTBEAT_DB_S)


def main() -> None:
    # SQL-native (decomiso Mongo 2026-06-29): el motor NO toca Mongo. Persiste todo en
    # operaciones.{ordenes_live,ordenes_audit,motor_heartbeat} (SQL). Sin handle Mongo.
    handler = _make_er_handler()
    account, env = inicializar_para_motor(handler)
    logger.info("Motor de órdenes ARRIBA (cuenta=%s, env=%s)", account, env.name)

    # El motor escucha solo la cuenta master para audit general. Las
    # operativas de mesa (operativa_mep) ya no dependen del WS — confirman
    # los fills vía REST `pyRofex.get_order_status` directo al broker.
    _recovery(account)

    # Heartbeat para monitoreo desde /manager → DIAG.
    threading.Thread(
        target=_heartbeat_loop,
        args=(account,),
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
