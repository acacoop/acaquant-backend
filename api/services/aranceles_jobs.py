"""api/services/aranceles_jobs.py — runner + persistencia del backfill de aranceles.

Servicio PURO (sin FastAPI) detrás de Manager → BOLETOS (tabs FALTANTES y
BACKFILL del router api/routers/manager/aunesa.py, que queda como plumbing).

El backfill matchea boletos de SQL operaciones.negocio_movimientos contra la
API /informes de Aunesa (api/services/aunesa_aranceles.run_backfill). Corre en
un thread daemon del proceso api.service y persiste progreso en SQL
`aranceles_job_runs` a cada cuenta procesada. Si el proceso se reinicia, la
fila queda con `updated_at` viejo → `estado_job` la marca `stale` (5 min sin
update) y el front permite re-disparar. NO usa systemd unit aparte: si el
volumen lo requiere, mover a `jobs/aranceles.py` + cron es 30 min más.

Errores de input: ValueError (→ 400 en el router) / LookupError (→ 404).
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from psycopg.rows import dict_row

from api.services._negocio_arancelables import OP_NO_ARANCELABLES
from api.services._negocio_futuros import EXCLUIR_UNIDADES_FUTUROS
from api.services.aunesa_aranceles import run_backfill
from core.pg_mirror import write_native
from core.postgres import get_pool

logger = logging.getLogger("api.services.aranceles_jobs")

_JOBS_TABLE = "aranceles_job_runs"
_STALE_S = 300  # 5 min sin update → marca el job como `stale` (proceso reiniciado).

# Columnas fijas de aranceles_job_runs; todo lo demás (cuentas, workers,
# cuentas_total/done, stats, ejemplos, errores, error) vive en el jsonb `data`.
_UI_FIXED_COLS = ("id", "status", "actor", "desde", "hasta", "apply",
                  "started_at", "updated_at", "finished_at")


# ── BOLETOS → tab FALTANTES ───────────────────────────────────────────────────

_FALTANTES_COLS = (
    "comprobante", "id_cuenta", "cuenta", "to_char(fecha, 'YYYY-MM-DD') AS fecha",
    "categoria", "op", "informacion", "moneda", "ticker", "unidad",
    "importe", "arancel",
)


def boletos_faltantes(desde: str, hasta: str, id_cuenta: str | None = None,
                      limit: int = 2000) -> dict[str, Any]:
    """Boletos sin arancel en el rango. Devuelve filas + agregado por (categoria, op).

    Considera "sin arancel": `arancel` null o ≤ 0. Excluye futuros DLR (USDL,
    no arancelables) y las ops confirmadas por el user como "tratamiento sin
    arancel" (Cash dividend, Interest payment, cauciones apertura, FCI, etc)."""
    try:
        date.fromisoformat(desde)
        date.fromisoformat(hasta)
    except ValueError as e:
        raise ValueError(f"fecha mal formada: {e}") from e
    if desde > hasta:
        raise ValueError("desde > hasta")

    # "Sin arancel" = columna null o ≤ 0. NULL-safe en las exclusiones.
    where = (
        "fecha >= %(desde)s AND fecha <= %(hasta)s "
        "AND (arancel IS NULL OR arancel <= 0) "
        "AND (unidad IS NULL OR unidad <> ALL(%(futs)s)) "
        "AND (op IS NULL OR op <> ALL(%(ops)s))"
    )
    params: dict[str, Any] = {
        "desde": desde, "hasta": hasta,
        "futs": list(EXCLUIR_UNIDADES_FUTUROS), "ops": list(OP_NO_ARANCELABLES),
    }
    if id_cuenta:
        where += " AND id_cuenta = %(idc)s"
        params["idc"] = str(id_cuenta)

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        # Boletos detallados (capped).
        cur.execute(
            f"SELECT {', '.join(_FALTANTES_COLS)} FROM negocio_movimientos "
            f"WHERE {where} ORDER BY fecha DESC LIMIT %(lim)s",
            {**params, "lim": limit})
        boletos = cur.fetchall()
        for b in boletos:
            if b.get("importe") is not None:
                b["importe"] = float(b["importe"])
            if b.get("arancel") is not None:
                b["arancel"] = float(b["arancel"])

        # Agregado por (categoria, op) sobre TODO el rango — sin cap, para que
        # el resumen sea fiel aunque la tabla detallada esté truncada.
        cur.execute(
            f"SELECT categoria, op, count(*) AS n, "
            f"SUM(abs(COALESCE(importe, 0))) AS importe_abs, "
            f"count(DISTINCT id_cuenta) AS n_cuentas "
            f"FROM negocio_movimientos WHERE {where} "
            f"GROUP BY categoria, op ORDER BY n DESC",
            params)
        por_categoria_op = cur.fetchall()

    n_total = sum(int(r["n"]) for r in por_categoria_op)
    return {
        "desde": desde, "hasta": hasta, "id_cuenta": id_cuenta,
        "n_total":   n_total,
        "truncado":  len(boletos) >= limit,
        "limit":     limit,
        "resumen": [
            {
                "categoria":   r.get("categoria"),
                "op":          r.get("op"),
                "n":           int(r["n"]),
                "importe_abs": float(r.get("importe_abs") or 0.0),
                "n_cuentas":   int(r.get("n_cuentas") or 0),
            }
            for r in por_categoria_op
        ],
        "boletos": boletos,
    }


# ── BOLETOS → tab BACKFILL (runner en thread + persistencia SQL) ─────────────


def _ui_row(doc: dict) -> dict:
    """Parte el doc lógico del job en la fila SQL: columnas fijas + `data` jsonb
    con el resto. Cada escritura reescribe la fila completa (upsert por id)."""
    row = {k: doc.get(k) for k in _UI_FIXED_COLS}
    row["data"] = {k: v for k, v in doc.items() if k not in _UI_FIXED_COLS}
    return row


def _write_job(doc: dict) -> None:
    """Upsert de la fila completa en SQL (best-effort, nunca levanta)."""
    write_native(_JOBS_TABLE, ["id"], [_ui_row(doc)])


def _read_job(job_id: str) -> dict | None:
    """Lee la fila y la reconstruye al shape lógico (columnas fijas + spread del
    jsonb `data`), con los datetimes como objetos (se serializan después)."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {', '.join(_UI_FIXED_COLS)}, data FROM {_JOBS_TABLE} "
            f"WHERE id = %s",
            (job_id,))
        r = cur.fetchone()
    if r is None:
        return None
    data = r.pop("data") or {}
    return {**r, **data}


def _serialize_job(doc: dict | None) -> dict | None:
    """Sin `id` (es UUID, ya está en `job_id`) y con datetimes ISO."""
    if doc is None:
        return None
    out = {k: v for k, v in doc.items() if k != "id"}
    for k in ("started_at", "updated_at", "finished_at"):
        if isinstance(out.get(k), datetime):
            out[k] = out[k].isoformat()
    return out


def _run_job(doc: dict, cuentas: list[str] | None, workers: int, apply: bool,
             desde_d: date, hasta_d: date) -> None:
    """Cuerpo del thread daemon. Reescribe la fila SQL completa a cada cuenta."""
    job_id = doc["id"]

    def on_progress(state: dict[str, Any]) -> None:
        doc["updated_at"]    = datetime.now(UTC)
        doc["cuentas_total"] = state["cuentas_total"]
        doc["cuentas_done"]  = state["cuentas_done"]
        doc["stats"] = {
            "inf":       state["inf"],
            "match":     state["match"],
            "sin_match": state["sin_match"],
            "escritos":  state["escritos"],
        }
        doc["ejemplos"] = state["ejemplos"]
        doc["errores"]  = state["errores"]
        _write_job(doc)

    try:
        run_backfill(
            desde=desde_d, hasta=hasta_d, cuentas=cuentas,
            workers=workers, apply=apply,
            on_progress=on_progress, progress_every=1,
        )
        doc["status"]      = "done"
        doc["finished_at"] = datetime.now(UTC)
        _write_job(doc)
    except Exception as e:
        logger.exception("backfill aranceles job %s falló", job_id)
        doc["status"]      = "error"
        doc["error"]       = str(e)
        doc["finished_at"] = datetime.now(UTC)
        _write_job(doc)


def iniciar_backfill(*, desde: str, hasta: str, cuentas: list[str] | None,
                     workers: int, apply: bool, actor: str) -> dict[str, Any]:
    """Arranca el backfill en un thread daemon y devuelve {job_id, status}."""
    try:
        desde_d = date.fromisoformat(desde)
        hasta_d = date.fromisoformat(hasta)
    except ValueError as e:
        raise ValueError(f"fecha mal formada: {e}") from e
    if desde_d > hasta_d:
        raise ValueError("desde > hasta")

    job_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    doc = {
        "id":            job_id,
        "status":        "running",
        "actor":         actor,
        "desde":         desde,
        "hasta":         hasta,
        "cuentas":       cuentas,
        "workers":       workers,
        "apply":         apply,
        "started_at":    now,
        "updated_at":    now,
        "finished_at":   None,
        "cuentas_total": 0,
        "cuentas_done":  0,
        "stats":         {"inf": 0, "match": 0, "sin_match": 0, "escritos": 0},
        "ejemplos":      [],
        "errores":       [],
        "error":         None,
    }
    _write_job(doc)

    threading.Thread(
        target=_run_job, args=(doc, cuentas, workers, apply, desde_d, hasta_d),
        daemon=True, name=f"aranceles-{job_id[:8]}",
    ).start()

    return {"job_id": job_id, "status": "running"}


def estado_job(job_id: str) -> dict[str, Any]:
    """Estado actual del job. Marca `stale` si lleva > 5 min sin update."""
    doc = _read_job(job_id)
    if doc is None:
        raise LookupError(f"job_id desconocido: {job_id}")
    if doc.get("status") == "running":
        updated = doc.get("updated_at")
        if isinstance(updated, datetime):
            # timestamptz vuelve aware desde Postgres; si fuera naive, asumir UTC.
            updated = updated if updated.tzinfo else updated.replace(tzinfo=UTC)
            if datetime.now(UTC) - updated > timedelta(seconds=_STALE_S):
                doc["status"] = "stale"
    return _serialize_job(doc) or {}


def historial_jobs(limit: int = 20) -> list[dict[str, Any]]:
    """Últimos N jobs (más reciente primero). Excluye los campos pesados
    `ejemplos`/`errores` del jsonb `data` con el operador `-` de Postgres."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {', '.join(_UI_FIXED_COLS)}, "
            f"(data - 'ejemplos' - 'errores') AS data FROM {_JOBS_TABLE} "
            f"ORDER BY started_at DESC LIMIT %s",
            (limit,))
        rows = cur.fetchall()
    out = []
    for r in rows:
        data = r.pop("data") or {}
        out.append(_serialize_job({**r, **data}))
    return [d for d in out if d]
