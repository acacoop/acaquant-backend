"""api/services/ordenes_sql.py — READ-SIDE del motor de órdenes leyendo Postgres.

Servicio PURO (sin FastAPI). Espejo SQL de las LECTURAS de `api/services/ordenes.py`
y `api/services/risk.py` que hoy leen Mongo (`Operaciones.{OrdenesLive,
AccountsDescubiertas}`). Dual-run: el selector del router elige SQL o Mongo según el
flag `ORDENES_SQL` (override por request `?_engine=sql|mongo`). El path Mongo queda
INTACTO → rollback = sacar el flag.

⚠️ Dominio TRANSACCIONAL en vivo. Esto NO toca el write-side ni el motor:
  * El motor (`engines/motor_ordenes.py`) y los services de envío/cancel
    (`ordenes.py::send_order/cancel`, `operativa_mep.py`) siguen escribiendo Mongo;
    el dual-write best-effort a SQL (flag ORDENES_SQL_WRITE) lo hace el write-side.
  * Acá SOLO se cambia de DÓNDE sale el doc LOCAL de la orden (Mongo OrdenesLive →
    SQL operaciones.ordenes_live). El **merge con el broker** (pyRofex
    get_all_orders_status), que es la verdad real-time, queda IDÉNTICO — usa el
    builder puro `_broker_report_to_local` del módulo Mongo (no se duplica el mapeo
    de cada execution report; sí se replica el bucle de merge para NO tocar
    `ordenes.py`, que es write-side y lo edita otra tarea).

Contrato de la tabla `operaciones.ordenes_live` (ver sql/schema.sql):
  cl_ord_id (PK) · account · ticker · estado · updated_at (timestamptz) · data (jsonb).
`data` = doc OrdenesLive completo con datetimes→ISO (doc_iso). El read reconstruye el
MISMO shape que devolvía Mongo (mismas claves, `created_at`/`updated_at` como datetime
para que el merge y el sort funcionen igual).
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from psycopg.rows import dict_row

from core.postgres import get_pool

logger = logging.getLogger("api.services.ordenes_sql")


def ordenes_sql_on() -> bool:
    """Lectura del read-side de órdenes desde SQL (default Mongo)."""
    return os.getenv("ORDENES_SQL") == "1"


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


def _q(sql: str, params: tuple) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# ── LECTURAS (espejo exacto de api/services/ordenes.py / risk.py) ─────────────
def get_order_status(cl_ord_id: str) -> dict[str, Any] | None:
    """Estado de una orden desde SQL `operaciones.ordenes_live`. Espejo de
    `ordenes.get_order_status` (find_one OrdenesLive, sin merge con broker)."""
    rows = _q(
        "SELECT cl_ord_id, account, ticker, estado, updated_at, data "
        "FROM operaciones.ordenes_live WHERE cl_ord_id = %s",
        (cl_ord_id,),
    )
    if not rows:
        return None
    return _doc_desde_row(rows[0])


def _orders_locales_dia(acc: str, inicio: datetime) -> list[dict]:
    """Docs LOCALES del día para la cuenta, desde SQL. Equivale al
    `find({account, created_at>=inicio})` del path Mongo. Filtra por `created_at`
    (vive en `data` jsonb, ISO) — NO por `updated_at` columnar, que se mueve con
    cada ER y traería órdenes de días previos tocadas hoy (cambiaría la semántica
    del path Mongo, que filtra por created_at)."""
    inicio_iso = inicio.astimezone(UTC).isoformat()
    rows = _q(
        "SELECT cl_ord_id, account, ticker, estado, updated_at, data "
        "FROM operaciones.ordenes_live "
        "WHERE account = %s AND (data->>'created_at') >= %s",
        (acc, inicio_iso),
    )
    return [_doc_desde_row(r) for r in rows]


def _merge_broker(acc: str, local: list[dict]) -> list[dict]:
    """Merge del set LOCAL (venga de SQL o Mongo) con lo que ve el broker AHORA.

    Réplica EXACTA del bloque de merge de `ordenes.list_orders_dia` — se mantiene
    acá (en vez de importarlo de `ordenes.py`) porque `ordenes.py` es write-side y
    lo edita otra tarea en paralelo; reusar el builder puro `_broker_report_to_local`
    evita duplicar el mapeo de cada execution report (que es lo único delicado). Si
    el broker falla, degradación graceful: se devuelve solo lo local."""
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
    """Órdenes del día — MISMO shape y MISMO merge que `ordenes.list_orders_dia`,
    pero el doc LOCAL sale de SQL `operaciones.ordenes_live` en vez de Mongo
    `Operaciones.OrdenesLive`. El merge con el broker (pyRofex) es la verdad
    real-time y queda idéntico."""
    from core.rofex_orders_session import cuenta_default

    acc = account or cuenta_default()
    if fecha is None:
        fecha = datetime.now(UTC)
    inicio = fecha.replace(hour=0, minute=0, second=0, microsecond=0)

    local = _orders_locales_dia(acc, inicio)
    return _merge_broker(acc, local)


# ── listado_cuentas — UNA fuente: clientes.cuentas (ver risk.listado_cuentas) ──
def listado_cuentas(solo_activas: bool = False) -> list[dict[str, Any]]:
    """Cuentas para operar = espejo de las comitentes de SQL `clientes.cuentas`. El
    descubrimiento al broker (`AccountsDescubiertas`) se eliminó — ya no hay dos paths:
    delega en `risk.listado_cuentas` (fuente única)."""
    from api.services.risk import listado_cuentas as _ls
    return _ls(solo_activas)
