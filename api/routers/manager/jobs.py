"""POST /jobs/run + GET /jobs/history, /jobs/history/stats, /jobs/{id}.

Orden de declaración importa: `/jobs/{job_id}` es un catch-all y debe
definirse DESPUÉS de `/jobs/history` y `/jobs/history/stats`, si no
FastAPI matchea "history" como job_id y nunca llega a los estáticos.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Body, HTTPException, Path, Query
from starlette.requests import Request

from api.ratelimit import limiter
from api.routers.manager._common import _AR_TZ, PROJECT_ROOT
from core.mongo import get_mongo_client_read

router = APIRouter()

# Job store in-process
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _set_job(job_id: str, **kwargs):
    with _jobs_lock:
        _jobs.setdefault(job_id, {}).update(kwargs)


_CMDS: dict[str, list[str]] = {
    "aum_backfill":    ["jobs.aum_backfill"],
    "aum_resumen_fci": ["jobs.aum_resumen_fci"],
    "cashflow":        ["jobs.cashflow", "--today"],
    "flujo":           ["jobs.flujo_contrapartes"],
    "bcra":            ["jobs.bcra", "--today"],
    "sync_api_copies": ["jobs.sync_api_copies", "--all"],
    "crear_indices":   ["scripts.crear_indices"],
    "cleanup_curvas":  ["jobs.cleanup_curvas", "--dry"],
}


@router.post("/jobs/run")
@limiter.limit("5/hour;20/day")
def run_job(
    request: Request,  # requerido por slowapi
    tipo: str = Body(..., embed=True),
    args: list[str] = Body(default=[], embed=True),
):
    if tipo not in _CMDS:
        raise HTTPException(400, f"Job desconocido: {tipo}. Disponibles: {list(_CMDS)}")

    job_id = str(uuid.uuid4())[:8]
    _set_job(job_id, status="running", tipo=tipo, started_at=datetime.utcnow().isoformat(), result=None)

    base = _CMDS[tipo]
    cmd = [sys.executable, "-m", base[0]] + base[1:] + args

    def worker():
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                cwd=PROJECT_ROOT, timeout=360,
            )
            out = (proc.stdout + "\n" + proc.stderr).strip()[-1500:]
            _set_job(job_id,
                status="done" if proc.returncode == 0 else "error",
                result=out, rc=proc.returncode,
                finished_at=datetime.utcnow().isoformat(),
            )
        except subprocess.TimeoutExpired:
            _set_job(job_id, status="error", result="Timeout (360s)", rc=-1,
                     finished_at=datetime.utcnow().isoformat())
        except Exception as e:
            _set_job(job_id, status="error", result=str(e), rc=-1,
                     finished_at=datetime.utcnow().isoformat())

    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job_id}


@router.get("/jobs/history")
def get_jobs_history(
    tipo: str | None = Query(None, description="Filtrar por tipo de job (carteras, aum, ...)"),
    status: str | None = Query(None, description="ok | partial | error"),
    desde: str | None = Query(None, description="ISO datetime o YYYY-MM-DD (UTC)"),
    hasta: str | None = Query(None, description="ISO datetime o YYYY-MM-DD (UTC)"),
    limit: int = Query(100, le=500),
):
    """Últimas corridas registradas en Manager.JobRuns. TTL 60d."""
    filtro: dict = {}
    if tipo:
        filtro["tipo"] = tipo
    if status:
        filtro["status"] = status
    if desde or hasta:
        rango: dict = {}

        def _parse(s: str) -> datetime:
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError:
                return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)

        if desde:
            rango["$gte"] = _parse(desde)
        if hasta:
            rango["$lte"] = _parse(hasta)
        filtro["started_at"] = rango

    docs = list(
        get_mongo_client_read()["Manager"]["JobRuns"]
        .find(filtro, {"_id": 0})
        .sort("started_at", -1)
        .limit(limit)
    )
    for d in docs:
        for k in ("started_at", "finished_at"):
            v = d.get(k)
            if isinstance(v, datetime):
                d[k] = (v if v.tzinfo else v.replace(tzinfo=UTC)) \
                    .astimezone(_AR_TZ).strftime("%Y-%m-%d %H:%M:%S")
    return docs


@router.get("/jobs/history/stats")
def get_jobs_history_stats(
    desde: str | None = Query(None, description="YYYY-MM-DD (default: últimos 7 días)"),
):
    """Resumen por tipo: runs totales, ok/partial/error y último run."""
    if desde:
        dt_desde = datetime.strptime(desde, "%Y-%m-%d").replace(tzinfo=UTC)
    else:
        dt_desde = datetime.now(UTC) - timedelta(days=7)

    pipeline = [
        {"$match": {"started_at": {"$gte": dt_desde}}},
        {"$group": {
            "_id": "$tipo",
            "total":   {"$sum": 1},
            "ok":      {"$sum": {"$cond": [{"$eq": ["$status", "ok"]},      1, 0]}},
            "partial": {"$sum": {"$cond": [{"$eq": ["$status", "partial"]}, 1, 0]}},
            "error":   {"$sum": {"$cond": [{"$eq": ["$status", "error"]},   1, 0]}},
            "last_run": {"$max": "$started_at"},
            "last_status": {"$last": "$status"},
        }},
        {"$sort": {"_id": 1}},
    ]
    rows = list(get_mongo_client_read()["Manager"]["JobRuns"].aggregate(pipeline))
    for r in rows:
        r["tipo"] = r.pop("_id")
        v = r.get("last_run")
        if isinstance(v, datetime):
            r["last_run"] = (v if v.tzinfo else v.replace(tzinfo=UTC)) \
                .astimezone(_AR_TZ).strftime("%Y-%m-%d %H:%M:%S")
    return rows


# Catch-all, DEBE ir después de /jobs/history y /jobs/history/stats.
@router.get("/jobs/{job_id}")
def get_job_status(job_id: str = Path(...)):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    return job
