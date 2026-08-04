"""api/services/ordenes_sql.py — READ-SIDE del motor de órdenes (única implementación).

Servicio PURO (sin FastAPI). Acá viven las LECTURAS de órdenes: estado de una
orden y listado del día con merge broker. El write-side (send/cancel/audit +
sesión pyRofex) vive en `api/services/ordenes.py`, que también aporta el builder
puro `_broker_report_to_local` (mapeo de cada execution report — lo único
delicado, no se duplica).

⚠️ Dominio TRANSACCIONAL en vivo. Esto NO toca el write-side ni el motor
(`engines/motor_ordenes.py`, único que escribe los ER). El **merge con el
broker** (pyRofex get_all_orders_status) es la verdad real-time.

Contrato de la tabla `operaciones.ordenes_live` (ver sql/schema.sql):
  cl_ord_id (PK) · account · ticker · estado · updated_at (timestamptz) · data (jsonb).
`data` = doc completo de la orden con datetimes→ISO (doc_iso). El read rehidrata
`created_at`/`updated_at` a datetime para que el merge y el sort funcionen tipados.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from api.services._sql import _q

logger = logging.getLogger("api.services.ordenes_sql")


# ── helpers de tipado ─────────────────────────────────────────────────────────
def _dt(v: Any) -> Any:
    """ISO string (de `data` jsonb) → datetime aware UTC. Deja pasar lo que ya
    sea datetime o None. El merge/sort de órdenes ordena por `created_at` y lo
    compara contra `datetime.min` aware → tiene que ser datetime, no str."""
    if v is None or isinstance(v, datetime):
        return v
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d


def _doc_desde_row(row: dict) -> dict:
    """Reconstruye el doc LOCAL de la orden (shape de OrdenesLive Mongo, sin `_id`)
    a partir de la fila SQL. `data` jsonb trae el doc completo passthrough; las
    columnas materializadas (account/ticker/estado/updated_at) son redundantes pero
    se respetan como fallback si `data` viniera incompleto en algún writer.
    `created_at`/`updated_at`/`last_er_ts` se rehidratan a datetime."""
    doc = dict(row.get("data") or {})
    doc.pop("_id", None)
    # Garantizar las claves clave aunque `data` venga parcial.
    doc.setdefault("cl_ord_id", row.get("cl_ord_id"))
    if row.get("account") is not None:
        doc.setdefault("account", row.get("account"))
    if row.get("ticker") is not None:
        doc.setdefault("ticker", row.get("ticker"))
    if row.get("estado") is not None:
        doc.setdefault("status", row.get("estado"))
    # Fechas → datetime (el merge con broker y el sort las necesitan tipadas).
    for k in ("created_at", "updated_at", "last_er_ts"):
        if k in doc:
            doc[k] = _dt(doc[k])
    # updated_at columnar es la fuente más fresca si `data` no lo trae.
    if doc.get("updated_at") is None and row.get("updated_at") is not None:
        doc["updated_at"] = _dt(row.get("updated_at"))
    return doc


# ── LECTURAS ──────────────────────────────────────────────────────────────────
def get_order_status(cl_ord_id: str) -> dict[str, Any] | None:
    """Estado de una orden desde SQL `operaciones.ordenes_live` (sin merge con
    broker — lo mantiene el motor con cada ER)."""
    rows = _q(
        "SELECT cl_ord_id, account, ticker, estado, updated_at, data "
        "FROM operaciones.ordenes_live WHERE cl_ord_id = %s",
        (cl_ord_id,),
    )
    if not rows:
        return None
    return _doc_desde_row(rows[0])


def _orders_locales_dia(acc: str, inicio: datetime) -> list[dict]:
    """Docs LOCALES del día para la cuenta. Filtra por `created_at` (vive en
    `data` jsonb, ISO) — NO por `updated_at` columnar, que se mueve con cada ER
    y traería órdenes de días previos tocadas hoy."""
    inicio_iso = inicio.astimezone(UTC).isoformat()
    rows = _q(
        "SELECT cl_ord_id, account, ticker, estado, updated_at, data "
        "FROM operaciones.ordenes_live "
        "WHERE account = %s AND (data->>'created_at') >= %s",
        (acc, inicio_iso),
    )
    return [_doc_desde_row(r) for r in rows]


def _merge_broker(acc: str, local: list[dict]) -> list[dict]:
    """Merge del set LOCAL con lo que ve el broker AHORA.

    pyRofex devuelve UNA entry por cada cambio de estado; cada cancel request
    genera una entry nueva con su propio clOrdId que apunta al original vía
    `origClOrdId`. Se resuelve la cadena hasta la raíz y por raíz gana el ER de
    mayor transactTime → el frontend ve UNA fila por orden con el estado actual.
    Si el broker falla, degradación graceful: se devuelve solo lo local."""
    import pyRofex

    from api.services.ordenes import _broker_report_to_local

    by_cl_ord: dict[str, dict[str, Any]] = {
        o["cl_ord_id"]: o for o in local if o.get("cl_ord_id")
    }
    try:
        resp = pyRofex.get_all_orders_status(account=acc)
        if resp and resp.get("status") == "OK":
            reports = []
            for o in resp.get("orders", []) or []:
                rep = o.get("orderReport", o)
                if rep.get("clOrdId"):
                    reports.append(rep)

            parent_of = {r["clOrdId"]: r.get("origClOrdId") for r in reports}

            def _root(cid: str, depth: int = 0) -> str:
                if depth > 20:
                    return cid
                parent = parent_of.get(cid)
                if not parent or parent == cid:
                    return cid
                return _root(parent, depth + 1)

            by_root: dict[str, tuple[str, dict]] = {}
            for rep in reports:
                cid = rep["clOrdId"]
                root = _root(cid)
                tt = str(rep.get("transactTime") or "")
                if root in by_root and by_root[root][0] >= tt:
                    continue
                by_root[root] = (tt, rep)

            for root, (_tt, rep) in by_root.items():
                mapped = _broker_report_to_local(rep)
                mapped["cl_ord_id"] = root
                if root in by_cl_ord:
                    existing = by_cl_ord[root]
                    mapped["external"] = False
                    if existing.get("actor_email"):
                        mapped["actor_email"] = existing["actor_email"]
                    if existing.get("proprietary") and not mapped.get("proprietary"):
                        mapped["proprietary"] = existing["proprietary"]
                    if existing.get("created_at") and not mapped.get("created_at"):
                        mapped["created_at"] = existing["created_at"]
                    by_cl_ord[root] = {**existing, **mapped}
                else:
                    by_cl_ord[root] = mapped
    except Exception as e:
        logger.warning("list_orders_dia (SQL): merge broker falló (acc=%s): %s", acc, e)

    out = list(by_cl_ord.values())
    out.sort(key=lambda x: x.get("created_at") or datetime.min.replace(tzinfo=UTC), reverse=True)
    return out


def list_orders_dia(account: str | None = None, fecha: datetime | None = None) -> list[dict]:
    """Órdenes del día: doc LOCAL desde SQL `operaciones.ordenes_live` + merge
    con el broker (pyRofex), que es la verdad real-time."""
    from core.rofex_orders_session import cuenta_default, resolver_cuenta_rofex

    # El account llega crudo de clientes.cuentas → traducir al nº que ROFEX acepta
    # (medido + cacheado; algunas crudas, otras con cero a la izquierda, sin regla).
    acc = resolver_cuenta_rofex(account or cuenta_default())
    if fecha is None:
        fecha = datetime.now(UTC)
    inicio = fecha.replace(hour=0, minute=0, second=0, microsecond=0)

    local = _orders_locales_dia(acc, inicio)
    return _merge_broker(acc, local)


