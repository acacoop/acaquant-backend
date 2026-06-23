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

from pymongo import ASCENDING

from core.mongo import get_mongo_client, get_mongo_client_read

logger = logging.getLogger("core.brackets")

DB = "Operaciones"
COL = "BracketsLive"

STATUS_PENDING_ENTRY = "PENDING_ENTRY"
STATUS_EXIT_SENT     = "EXIT_SENT"
STATUS_COMPLETED     = "COMPLETED"
STATUS_ENTRY_CANCELLED = "ENTRY_CANCELLED"
STATUS_EXIT_REJECTED = "EXIT_REJECTED"

# Estados de orden de pyRofex que cuentan como "entrada lista para disparar salida".
ENTRY_FILLED = {"FILLED"}
# Estados terminales que matan el bracket sin disparar salida.
ENTRY_DEAD = {"REJECTED", "CANCELLED", "EXPIRED"}


def _coll():
    return get_mongo_client()[DB][COL]


def ensure_indexes() -> None:
    col = _coll()
    existing = {ix["name"] for ix in col.list_indexes()}
    if "entry_cl_ord_id_1" not in existing:
        col.create_index(
            [("entry_cl_ord_id", ASCENDING)], name="entry_cl_ord_id_1", unique=True,
        )
    if "status_account_1" not in existing:
        col.create_index([("status", ASCENDING), ("account", ASCENDING)],
                         name="status_account_1")


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
    now = datetime.now(UTC)
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
    _coll().insert_one(doc)
    _mirror_bracket_sql(entry_cl_ord_id)
    return doc


def _mirror_bracket_sql(entry_cl_ord_id: str | None) -> None:
    """Espejo best-effort BracketsLive→`operaciones.brackets_live` (flag ORDENES_SQL_WRITE).
    PK = entry_cl_ord_id (columna cl_ord_id). Read-back, low-freq, NUNCA levanta."""
    try:
        from core import pg_mirror
        if not pg_mirror.ordenes_on() or not entry_cl_ord_id:
            return
        d = _coll().find_one({"entry_cl_ord_id": entry_cl_ord_id}, {"_id": 0})
        if d:
            pg_mirror.mirror_ordenes("operaciones.brackets_live", ["cl_ord_id"], [{
                "cl_ord_id": entry_cl_ord_id, "account": d.get("account"),
                "estado": d.get("status"), "data": pg_mirror.doc_iso(d)}])
    except Exception:
        pass


def find_pending_by_entry(entry_cl_ord_id: str) -> dict[str, Any] | None:
    """Lookup que el motor hace en cada ER. Si devuelve algo, hay que disparar."""
    return _coll().find_one(
        {"entry_cl_ord_id": entry_cl_ord_id, "status": STATUS_PENDING_ENTRY},
        {"_id": 0},
    )


def mark_exit_sent(
    entry_cl_ord_id: str,
    *,
    exit_cl_ord_id: str,
    exit_proprietary: str | None,
) -> None:
    _coll().update_one(
        {"entry_cl_ord_id": entry_cl_ord_id},
        {"$set": {
            "exit_cl_ord_id":   exit_cl_ord_id,
            "exit_proprietary": exit_proprietary,
            "status":           STATUS_EXIT_SENT,
            "updated_at":       datetime.now(UTC),
        }},
    )
    _mirror_bracket_sql(entry_cl_ord_id)


def mark_entry_dead(entry_cl_ord_id: str, status_broker: str) -> None:
    """Entrada terminal sin FILL → no disparamos salida."""
    _coll().update_one(
        {"entry_cl_ord_id": entry_cl_ord_id, "status": STATUS_PENDING_ENTRY},
        {"$set": {
            "status":           STATUS_ENTRY_CANCELLED,
            "entry_final_status": status_broker,
            "updated_at":       datetime.now(UTC),
        }},
    )
    _mirror_bracket_sql(entry_cl_ord_id)


def mark_exit_rejected(entry_cl_ord_id: str, reason: str | None) -> None:
    _coll().update_one(
        {"entry_cl_ord_id": entry_cl_ord_id},
        {"$set": {
            "status":         STATUS_EXIT_REJECTED,
            "exit_error":     reason,
            "updated_at":     datetime.now(UTC),
        }},
    )
    _mirror_bracket_sql(entry_cl_ord_id)


def mark_completed(exit_cl_ord_id: str) -> None:
    """Cuando la salida llega FILLED."""
    _coll().update_one(
        {"exit_cl_ord_id": exit_cl_ord_id, "status": STATUS_EXIT_SENT},
        {"$set": {"status": STATUS_COMPLETED, "updated_at": datetime.now(UTC)}},
    )
    _b = find_by_exit(exit_cl_ord_id)
    _mirror_bracket_sql(_b.get("entry_cl_ord_id") if _b else None)


def find_by_exit(exit_cl_ord_id: str) -> dict[str, Any] | None:
    return _coll().find_one(
        {"exit_cl_ord_id": exit_cl_ord_id},
        {"_id": 0},
    )


def list_dia(account: str | None = None) -> list[dict[str, Any]]:
    filtro: dict = {}
    if account:
        filtro["account"] = account
    return list(
        get_mongo_client_read()[DB][COL]
        .find(filtro, {"_id": 0})
        .sort("created_at", -1)
        .limit(200)
    )
