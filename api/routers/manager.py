"""Manager API: diagnóstico de motores/jobs, backfills, changelog, latencia."""
import os
import subprocess
import sys
import threading
import time as _time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, HTTPException, Path, Query

from core.mongo import get_mongo_client, get_mongo_client_read

router = APIRouter(prefix="/api/manager", tags=["Manager"])

_AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Job store in-process ──────────────────────────────────────────────────────
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _set_job(job_id: str, **kwargs):
    with _jobs_lock:
        _jobs.setdefault(job_id, {}).update(kwargs)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _fmt_delta(s: float) -> str:
    s = int(s)
    if s < 0:     return "—"
    if s < 60:    return f"{s}s"
    if s < 3600:  return f"{s // 60}m {s % 60}s"
    if s < 86400: return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}d {(s % 86400) // 3600}h"


def _fetch_last(db_name: str, coll: str, field: str, filtro: dict):
    client = get_mongo_client_read()
    return client[db_name][coll].find_one(filtro, {field: 1, "_id": 0}, sort=[(field, -1)])


def _es_rueda(ahora_ar: datetime) -> bool:
    return ahora_ar.weekday() < 5 and time(10, 0) <= ahora_ar.time() <= time(17, 5)


# ── Status config ─────────────────────────────────────────────────────────────
_MOTORES = [
    ("Trading",  "TimeSales",       "timestamp",  "TimeSales (rofex)",    300, _AR_TZ),
    ("Trading",  "MarketSnapshot",  "updated_at", "MarketSnapshot",       120, UTC),
    ("Trading",  "ForwardsLive",    "updated_at", "ForwardsLive",          60, UTC),
    ("Trading",  "BreakevensLive",  "updated_at", "BreakevensLive",        60, UTC),
    ("Opciones", "OptionsSnapshot", "updated_at", "OptionsSnapshot",      180, UTC),
]

_JOBS_STATUS = [
    ("Trading",     "CER",         "fecha",          "iso",      "CER (BCRA)",           2, "diario 20:00 UTC"),
    ("Trading",     "DOLAR",       "fecha",           "iso",      "DOLAR (BCRA)",         2, "diario 20:00 UTC"),
    ("Valuaciones", "AuM",         "fecha_snapshot",  "iso",      "AuM snapshot",         2, "diario 23:00 UTC L-V"),
    ("Valuaciones", "Carteras",    "timestamp",       "datetime", "Carteras (Aunesa)",     1, "4x / día hábil"),
    ("CashFlow",    "Movimientos", "fecha",           "ddmmyyyy", "CashFlow Movimientos",  2, "02:00 UTC mar-sáb"),
    ("CashFlow",    "Flujo",       "concertacion",    "iso",      "Flujo Contrapartes",    2, "diario 22:00 UTC L-V"),
]


def _parse_ts(val, tipo: str) -> datetime | None:
    if val is None:
        return None
    try:
        if isinstance(val, datetime):
            return val if val.tzinfo else val.replace(tzinfo=UTC)
        s = str(val)[:10]
        from datetime import date as _d
        d = _d.fromisoformat(s) if tipo in ("iso", "datetime") else datetime.strptime(s, "%d/%m/%Y").date()
        return datetime(d.year, d.month, d.day, tzinfo=_AR_TZ)
    except Exception:
        return None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/status")
def get_status():
    ahora = datetime.now(_AR_TZ)
    rueda = _es_rueda(ahora)

    def check_motor(s):
        db_n, coll, field, nombre, umbral, tz_naive = s
        doc = _fetch_last(db_n, coll, field, {field: {"$exists": True}})
        if not doc or not doc.get(field):
            return {"nombre": nombre, "ultima": None, "hace": "—", "umbral": umbral, "estado": "sin_datos"}
        ts = doc[field]
        if isinstance(ts, datetime) and ts.tzinfo is None:
            ts = ts.replace(tzinfo=tz_naive)
        delta = (ahora.astimezone(UTC) - ts.astimezone(UTC)).total_seconds()
        if not rueda:       estado = "fuera_rueda"
        elif delta > umbral * 3: estado = "critico"
        elif delta > umbral:     estado = "lento"
        else:                    estado = "ok"
        return {
            "nombre": nombre,
            "ultima": ts.astimezone(_AR_TZ).strftime("%H:%M:%S"),
            "hace":   _fmt_delta(delta),
            "umbral": umbral,
            "estado": estado,
        }

    def check_job(s):
        db_n, coll, field, tipo, nombre, umbral_dias, freq = s
        doc = _fetch_last(db_n, coll, field, {field: {"$exists": True, "$nin": [None, ""]}})
        if not doc:
            return {"nombre": nombre, "ultimo": None, "hace": "—", "frecuencia": freq, "estado": "sin_datos"}
        ts = _parse_ts(doc.get(field), tipo)
        if not ts:
            return {"nombre": nombre, "ultimo": str(doc.get(field))[:19], "hace": "—", "frecuencia": freq, "estado": "error_parse"}
        delta_dias = (ahora.date() - ts.date()).days
        estado = "ok" if delta_dias <= 0 else ("atrasado" if delta_dias <= umbral_dias else "critico")
        return {
            "nombre":    nombre,
            "ultimo":    ts.astimezone(_AR_TZ).strftime("%Y-%m-%d %H:%M"),
            "hace":      _fmt_delta((ahora - ts.astimezone(_AR_TZ)).total_seconds()),
            "frecuencia": freq,
            "estado":    estado,
        }

    with ThreadPoolExecutor(max_workers=8) as ex:
        motores = list(ex.map(check_motor, _MOTORES))
        jobs    = list(ex.map(check_job,   _JOBS_STATUS))

    return {
        "ahora_ar": ahora.strftime("%Y-%m-%d %H:%M:%S"),
        "en_rueda": rueda,
        "motores":  motores,
        "jobs":     jobs,
    }


# ── Job execution ─────────────────────────────────────────────────────────────
_CMDS: dict[str, list[str]] = {
    "aum_backfill":    ["jobs.aum_backfill"],
    "aum_resumen_fci": ["jobs.aum_resumen_fci"],
    "carteras":        ["jobs.carteras"],
    "cashflow":        ["jobs.cashflow", "--today"],
    "flujo":           ["jobs.flujo_contrapartes"],
    "bcra":            ["jobs.bcra", "--today"],
    "sync_api_copies": ["jobs.sync_api_copies", "--all"],
    "crear_indices":   ["scripts.crear_indices"],
    "cleanup_curvas":  ["jobs.cleanup_curvas", "--dry"],
}


@router.post("/jobs/run")
def run_job(
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


@router.get("/jobs/{job_id}")
def get_job_status(job_id: str = Path(...)):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    return job


# ── ChangeLog ─────────────────────────────────────────────────────────────────
@router.get("/changelog")
def get_changelog(limit: int = Query(100, le=500)):
    docs = list(
        get_mongo_client_read()["Manager"]["ChangeLog"]
        .find({}, {"_id": 0})
        .sort("when", -1)
        .limit(limit)
    )
    for d in docs:
        if isinstance(d.get("when"), datetime):
            d["when"] = d["when"].astimezone(_AR_TZ).strftime("%Y-%m-%d %H:%M:%S")
    return docs


# ── Latencia benchmark ────────────────────────────────────────────────────────
@router.get("/latencia")
def benchmark_latencia():
    client = get_mongo_client_read()
    resultados = []

    def medir(vista: str, db_name: str, coll: str, query: dict | None = None, descripcion: str = ""):
        t0 = _time.perf_counter()
        docs = list(client[db_name][coll].find(query or {}, {"_id": 0}))
        ms = (_time.perf_counter() - t0) * 1000
        resultados.append({
            "vista": vista, "coleccion": coll, "descripcion": descripcion,
            "docs": len(docs), "ms": round(ms, 1),
            "ms_doc": round(ms / max(len(docs), 1), 3),
        })

    desde_24h = datetime.now(UTC) - timedelta(hours=24)

    medir("Mercado",          "Trading",     "MarketSnapshot",   descripcion="Snapshot tickers en tiempo real")
    medir("Mercado",          "Trading",     "TimeSales",        {"timestamp": {"$gte": desde_24h}}, "Trades últimas 24h")
    medir("Forwards",         "Trading",     "ForwardsLive",     descripcion="Matriz forward live")
    medir("Breakevens",       "Trading",     "BreakevensLive",   descripcion="Breakevens live")
    medir("Forwards hist.",   "Trading",     "ForwardsHistorico",descripcion="Historial forwards")
    medir("Breakevens hist.", "Trading",     "BreakevensHistorico", descripcion="Historial breakevens")
    medir("BCRA",             "Trading",     "CER",              descripcion="Serie CER histórica")
    medir("BCRA",             "Trading",     "DOLAR",            descripcion="Dólar A3500")
    medir("Curvas",           "Trading",     "Curvas",           descripcion="Definición instrumentos renta fija")
    medir("Opciones",         "Opciones",    "OptionsSnapshot",  descripcion="Snapshot opciones GGAL")
    medir("AuM (full)",       "Valuaciones", "AuM",              descripcion="Todos los snapshots históricos")
    medir("Carteras",         "Valuaciones", "Carteras",         descripcion="Posiciones mes actual")
    medir("Assets",           "Valuaciones", "Assets",           descripcion="Metadata instrumentos")
    medir("CashFlow Mov.",    "CashFlow",    "Movimientos",      descripcion="Historial movimientos dinero")
    medir("Flujo CP",         "CashFlow",    "Flujo",            descripcion="Operaciones por contraparte")

    resultados.sort(key=lambda r: -r["ms"])
    return {
        "total_ms":   round(sum(r["ms"] for r in resultados), 1),
        "total_docs": sum(r["docs"] for r in resultados),
        "queries":    len(resultados),
        "resultados": resultados,
    }
