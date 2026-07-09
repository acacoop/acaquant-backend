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
from api.routers.manager._common import PROJECT_ROOT

router = APIRouter()


# Job store in-process
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _set_job(job_id: str, **kwargs):
    with _jobs_lock:
        _jobs.setdefault(job_id, {}).update(kwargs)


_CMDS: dict[str, list[str]] = {
    "cashflow":        ["jobs.cashflow", "--today"],
    "bcra":            ["jobs.bcra", "--today"],
    "crear_indices":   ["scripts.crear_indices"],
    "cleanup_curvas":  ["jobs.cleanup_curvas", "--dry"],
    "backfill_tasas":  ["jobs.backfill_tasas"],
    # Auto-control de calidad de datos: re-corre los 5 controles y actualiza
    # manager.controles_datos (sin Telegram — botón "CORRER AHORA" de la tab).
    "controles_datos": ["jobs.controles_datos", "--no-telegram"],
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


def _parse_dt(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)


@router.get("/jobs/catalogo")
def get_jobs_catalogo():
    """Catálogo COMPLETO de jobs agendados: se parsea deploy/crontab.txt en
    runtime (nunca desactualizado) + último run por módulo desde job_runs.
    Lo consume Manager → OBSERVABILIDAD → JOBS."""
    from api.services.jobs_catalogo import catalogo_jobs
    return catalogo_jobs()


@router.get("/jobs/history")
def get_jobs_history(
    tipo: str | None = Query(None, description="Filtrar por tipo de job (carteras, aum, ...)"),
    status: str | None = Query(None, description="ok | partial | error"),
    desde: str | None = Query(None, description="ISO datetime o YYYY-MM-DD (UTC)"),
    hasta: str | None = Query(None, description="ISO datetime o YYYY-MM-DD (UTC)"),
    limit: int = Query(100, le=500),
    _engine: str | None = Query(None, include_in_schema=False),
):
    """Últimas corridas registradas en manager.job_runs (SQL-native, decomiso Mongo). TTL 60d."""
    from api.services import manager_infra_sql
    return manager_infra_sql.jobs_history_sql(
        tipo=tipo, status=status,
        desde=_parse_dt(desde) if desde else None,
        hasta=_parse_dt(hasta) if hasta else None,
        limit=limit,
    )


@router.get("/jobs/history/stats")
def get_jobs_history_stats(
    desde: str | None = Query(None, description="YYYY-MM-DD (default: últimos 7 días)"),
    _engine: str | None = Query(None, include_in_schema=False),
):
    """Resumen por tipo: runs totales, ok/partial/error y último run."""
    if desde:
        dt_desde = datetime.strptime(desde, "%Y-%m-%d").replace(tzinfo=UTC)
    else:
        dt_desde = datetime.now(UTC) - timedelta(days=7)

    from api.services import manager_infra_sql
    return manager_infra_sql.jobs_history_stats_sql(desde=dt_desde)


# Catch-all, DEBE ir después de /jobs/history y /jobs/history/stats.
@router.get("/jobs/{job_id}")
def get_job_status(job_id: str = Path(...)):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    return job
