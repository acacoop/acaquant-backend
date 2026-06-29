"""Brackets — entrada LIMIT + salida automática cuando la entrada se llena.

Modelo simple: el user manda una orden de entrada (BUY o SELL) y un precio
de salida. Cuando la entrada llega a FILLED por order_report, el motor
de órdenes dispara la salida (side opuesto, MISMA cantidad, LIMIT al
precio definido). Sin SL — solo TP.

Estados:
  - PENDING_ENTRY: orden de entrada al broker, esperando fill.
  - EXIT_SENT:     entrada FILLED, salida mandada al broker.
  - COMPLETED:     salida FILLED también. Ciclo cerrado.
  - ENTRY_CANCELLED: entrada rechazada/cancelada/expirada antes de FILL.
  - EXIT_REJECTED: salida rechazada (caso raro — la entrada se llenó pero
                   la salida no entra; queda posición abierta para
                   intervención manual).

Colección: `Operaciones.BracketsLive`. Sin TTL — la mesa los limpia o el
día siguiente quedan como histórico (estatus inmutable). El motor
solo procesa los que están en PENDING_ENTRY.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from core.postgres import get_pool

logger = logging.getLogger("core.brackets")

# SQL-native (decomiso 2026-06-29): operaciones.brackets_live (PK cl_ord_id = entry_cl_ord_id,
# + account/estado materializados + data jsonb). Antes Operaciones.BracketsLive (Mongo).
STATUS_PENDING_ENTRY = "PENDING_ENTRY"
STATUS_EXIT_SENT     = "EXIT_SENT"
STATUS_COMPLETED     = "COMPLETED"
STATUS_ENTRY_CANCELLED = "ENTRY_CANCELLED"
STATUS_EXIT_REJECTED = "EXIT_REJECTED"

# Estados de orden de pyRofex que cuentan como "entrada lista para disparar salida".
ENTRY_FILLED = {"FILLED"}
# Estados terminales que matan el bracket sin disparar salida.
ENTRY_DEAD = {"REJECTED", "CANCELLED", "EXPIRED"}


def ensure_indexes() -> None:
    """NO-OP — los índices de operaciones.brackets_live viven en sql/schema.sql."""
    return


def _write_bracket(entry_cl_ord_id: str, fields: dict) -> None:
    """Upsert SQL-native (read-modify-write) a operaciones.brackets_live. `fields` se mergea
    en el doc (insert inicial trae todo; updates solo lo que cambia)."""
    from core import pg_mirror
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT data FROM operaciones.brackets_live WHERE cl_ord_id = %s",
                    (entry_cl_ord_id,))
        row = cur.fetchone()
        merged = {**((row["data"] if row else None) or {}), **fields,
                  "entry_cl_ord_id": entry_cl_ord_id}
        cur.execute(
            "INSERT INTO operaciones.brackets_live (cl_ord_id, account, estado, data) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (cl_ord_id) DO UPDATE SET "
            "account = EXCLUDED.account, estado = EXCLUDED.estado, data = EXCLUDED.data",
            (entry_cl_ord_id, merged.get("account"), merged.get("status"),
             Jsonb(pg_mirror.doc_iso(merged))))
        conn.commit()


def create_bracket(
    *,
    entry_cl_ord_id: str,
    entry_proprietary: str | None,
    ticker: str,
    side_entry: str,       # "BUY" | "SELL"
    price_entry: float,
    size: int,
    price_exit: float,
    tif: str,
    account: str,
    actor_email: str | None,
) -> dict[str, Any]:
    """Inserta el doc post-envío exitoso de la orden de entrada."""
    now = datetime.now(UTC).isoformat()
    doc = {
        "entry_cl_ord_id":   entry_cl_ord_id,
        "entry_proprietary": entry_proprietary,
        "ticker":            ticker,
        "side_entry":        side_entry.upper(),
        "price_entry":       float(price_entry),
        "size":              int(size),
        "price_exit":        float(price_exit),
        "tif":               (tif or "DAY").upper(),
        "account":           account,
        "actor_email":       actor_email,
        "status":            STATUS_PENDING_ENTRY,
        "exit_cl_ord_id":    None,
        "exit_proprietary":  None,
        "created_at":        now,
        "updated_at":        now,
    }
    _write_bracket(entry_cl_ord_id, doc)
    return doc


def find_pending_by_entry(entry_cl_ord_id: str) -> dict[str, Any] | None:
    """Lookup que el motor hace en cada ER. Si devuelve algo, hay que disparar."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT data FROM operaciones.brackets_live WHERE cl_ord_id = %s "
                    "AND data->>'status' = %s", (entry_cl_ord_id, STATUS_PENDING_ENTRY))
        r = cur.fetchone()
    return r["data"] if r else None


def mark_exit_sent(
    entry_cl_ord_id: str,
    *,
    exit_cl_ord_id: str,
    exit_proprietary: str | None,
) -> None:
    _write_bracket(entry_cl_ord_id, {
        "exit_cl_ord_id": exit_cl_ord_id, "exit_proprietary": exit_proprietary,
        "status": STATUS_EXIT_SENT, "updated_at": datetime.now(UTC).isoformat()})


def mark_entry_dead(entry_cl_ord_id: str, status_broker: str) -> None:
    """Entrada terminal sin FILL → no disparamos salida. Solo si sigue PENDING_ENTRY."""
    if not find_pending_by_entry(entry_cl_ord_id):
        return
    _write_bracket(entry_cl_ord_id, {
        "status": STATUS_ENTRY_CANCELLED, "entry_final_status": status_broker,
        "updated_at": datetime.now(UTC).isoformat()})


def mark_exit_rejected(entry_cl_ord_id: str, reason: str | None) -> None:
    _write_bracket(entry_cl_ord_id, {
        "status": STATUS_EXIT_REJECTED, "exit_error": reason,
        "updated_at": datetime.now(UTC).isoformat()})


def mark_completed(exit_cl_ord_id: str) -> None:
    """Cuando la salida llega FILLED. Solo si el bracket está EXIT_SENT."""
    b = find_by_exit(exit_cl_ord_id)
    if not b or b.get("status") != STATUS_EXIT_SENT:
        return
    _write_bracket(b["entry_cl_ord_id"], {
        "status": STATUS_COMPLETED, "updated_at": datetime.now(UTC).isoformat()})


def find_by_exit(exit_cl_ord_id: str) -> dict[str, Any] | None:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT data FROM operaciones.brackets_live "
                    "WHERE data->>'exit_cl_ord_id' = %s", (exit_cl_ord_id,))
        r = cur.fetchone()
    return r["data"] if r else None


def list_dia(account: str | None = None) -> list[dict[str, Any]]:
    where, params = "", []
    if account:
        where = "WHERE account = %s"
        params = [account]
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT data FROM operaciones.brackets_live {where} "
                    "ORDER BY data->>'created_at' DESC LIMIT 200", params)
        return [r["data"] for r in cur.fetchall()]
