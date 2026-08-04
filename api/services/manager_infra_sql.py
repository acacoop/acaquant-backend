"""api/services/manager_infra_sql.py — lecturas SQL de la infra de Manager.

Espejo SQL (Postgres) de las lecturas que hoy hacen contra Mongo:
  - Manager.JobRuns   → manager.job_runs  (historial de corridas de jobs)
  - Manager.RoleAudit → manager.role_audit (auditoría de cambios de rol/usuario)

Funciones PURAS (sin FastAPI): replican EXACTAMENTE el shape que devuelven los
paths Mongo de `api/routers/manager/jobs.py` y `core/roles.list_audit`. El
es la única implementación (decomiso Mongo), con el
path Mongo intacto → rollback = sacar la env.

Reglas de traducción Mongo→SQL aplicadas:
  - El doc completo vive en `data` jsonb (doc_iso: datetimes→ISO). Para devolver
    el MISMO shape que Mongo (`find({}, {"_id": 0})`), partimos del `data` y
    sobrescribimos los timestamps con el valor columnar timestamptz (no el ISO
    crudo del jsonb), formateado a AR igual que el path Mongo.
  - started_at/finished_at/ts son timestamptz (el writer usa now(UTC) aware) →
    se formatean a AR (America/Argentina/Buenos_Aires) con el MISMO formato
    "%Y-%m-%d %H:%M:%S" que `jobs.py` / nada en audit (audit devuelve el doc tal
    cual, con ts ISO — ver list_audit, que NO reformatea).
"""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from api.services._sql import _q

_AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def _fmt_ar(v: datetime | None) -> datetime | str | None:
    """timestamptz → string AR 'YYYY-MM-DD HH:MM:SS' (igual que el path Mongo de jobs.py)."""
    if not isinstance(v, datetime):
        return v
    aware = v if v.tzinfo else v.replace(tzinfo=UTC)
    return aware.astimezone(_AR_TZ).strftime("%Y-%m-%d %H:%M:%S")


# ── JobRuns → /jobs/history ──────────────────────────────────────────────────
def jobs_history_sql(
    tipo: str | None = None,
    status: str | None = None,
    desde: datetime | None = None,
    hasta: datetime | None = None,
    limit: int = 100,
) -> list[dict]:
    """Últimas corridas. Mismo shape que el path Mongo: el doc completo (data jsonb)
    con started_at/finished_at reformateados a AR. Filtra por tipo/status/rango de
    started_at, ordena por started_at desc, limita."""
    where, params = [], []
    if tipo:
        where.append("tipo = %s")
        params.append(tipo)
    if status:
        where.append("status = %s")
        params.append(status)
    if desde is not None:
        where.append("started_at >= %s")
        params.append(desde)
    if hasta is not None:
        where.append("started_at <= %s")
        params.append(hasta)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    params.append(int(limit))
    rows = _q(
        f"SELECT started_at, finished_at, data FROM manager.job_runs{clause} "
        f"ORDER BY started_at DESC LIMIT %s",
        params,
    )
    out: list[dict] = []
    for r in rows:
        # El doc Mongo original (sin _id) está en `data`. Sobrescribimos los ts con
        # el valor columnar (autoritativo) reformateado a AR — idéntico al path Mongo.
        doc = dict(r["data"] or {})
        doc["started_at"] = _fmt_ar(r["started_at"])
        doc["finished_at"] = _fmt_ar(r["finished_at"])
        out.append(doc)
    return out


# ── JobRuns → /jobs/history/stats ────────────────────────────────────────────
def jobs_history_stats_sql(desde: datetime) -> list[dict]:
    """Resumen por tipo desde `desde` (started_at >=). Mismo shape que el path Mongo:
    [{tipo, total, ok, partial, error, last_run (AR str), last_status}]. last_status =
    el status del run más reciente (igual que $last sobre el orden natural Mongo —
    acá lo tomamos explícito del started_at máximo)."""
    rows = _q(
        """
        SELECT tipo,
               count(*)                                          AS total,
               count(*) FILTER (WHERE status = 'ok')             AS ok,
               count(*) FILTER (WHERE status = 'partial')        AS partial,
               count(*) FILTER (WHERE status = 'error')          AS error,
               max(started_at)                                   AS last_run,
               (array_agg(status ORDER BY started_at DESC))[1]   AS last_status
        FROM manager.job_runs
        WHERE started_at >= %s
        GROUP BY tipo
        ORDER BY tipo
        """,
        (desde,),
    )
    for r in rows:
        r["last_run"] = _fmt_ar(r["last_run"])
    return rows


# ── JobRuns → frescura por tipo (diagnostico._leer_frescura) ─────────────────
def jobrun_ultimo_sql(run_tipo: str) -> tuple[datetime | None, str | None]:
    """Último run de `run_tipo` por finished_at desc → (finished_at aware, status).
    (None, None) si no hay. Espeja el find_one de diagnostico._leer_frescura."""
    rows = _q(
        "SELECT finished_at, status FROM manager.job_runs WHERE tipo = %s "
        "ORDER BY finished_at DESC NULLS LAST LIMIT 1",
        (run_tipo,),
    )
    if not rows:
        return None, None
    ft = rows[0]["finished_at"]
    if isinstance(ft, datetime) and ft.tzinfo is None:
        ft = ft.replace(tzinfo=UTC)
    return ft, rows[0]["status"]

