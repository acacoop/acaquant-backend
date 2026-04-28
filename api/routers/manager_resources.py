"""Endpoints de recursos del servidor para el Manager.

- GET /api/manager/resources          → snapshot actual (CPU/RAM/swap/disk + procesos)
- GET /api/manager/resources/history  → últimos N snapshots (buffer en memoria)

El histórico se alimenta del background task `resources_sampler_loop`
(montado en el `lifespan` de `api/main.py`). Buffer: deque en memoria, 180
muestras (3h con resolución 1min).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

import psutil
from fastapi import APIRouter, Query

logger = logging.getLogger("api.resources")

router = APIRouter(prefix="/api/manager", tags=["Manager"])

# Histórico en memoria — deque circular (thread-safe para append/read simple).
_HISTORY_MAXLEN = 180  # 3 horas a 1 muestra/min
_history: deque[dict[str, Any]] = deque(maxlen=_HISTORY_MAXLEN)
_started = time.time()


# ── Identificación de procesos relevantes ─────────────────────────────────────
# Cada tupla: (label, cmdline substring to match).
_PROCESS_LABELS: list[tuple[str, str]] = [
    ("api",               "uvicorn api.main:app"),
    ("motor_rofex",       "engines.valores"),
    ("motor_options",     "engines.options"),
    ("motor_curvas",      "engines.curvas"),
    ("motor_forwards",    "engines.forwards"),
    ("motor_breakevens",  "engines.breakevens"),
    ("motor_ordenes",     "engines.motor_ordenes"),
    ("cloudflared",       "cloudflared"),
]


def _label_for(cmdline: str) -> str | None:
    for label, needle in _PROCESS_LABELS:
        if needle in cmdline:
            return label
    return None


def _snapshot_procesos() -> list[dict[str, Any]]:
    """Itera procesos y agrega los que matchean el mapping. Suma si hay múltiples."""
    agg: dict[str, dict[str, Any]] = {}
    for p in psutil.process_iter(["pid", "cmdline", "memory_info", "cpu_percent"]):
        try:
            info = p.info
            cmdline = " ".join(info.get("cmdline") or [])
            if not cmdline:
                continue
            label = _label_for(cmdline)
            if not label:
                continue
            mem = info.get("memory_info")
            rss_mb = (mem.rss / (1024 * 1024)) if mem else 0.0
            cpu = float(info.get("cpu_percent") or 0.0)
            if label in agg:
                agg[label]["rss_mb"] += rss_mb
                agg[label]["cpu_percent"] += cpu
                agg[label]["count"] += 1
            else:
                agg[label] = {
                    "label": label,
                    "pid": info.get("pid"),
                    "rss_mb": round(rss_mb, 1),
                    "cpu_percent": round(cpu, 1),
                    "count": 1,
                }
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Orden fijo según el mapping para que el front siempre los renderice igual.
    orden = [lbl for lbl, _ in _PROCESS_LABELS]
    out = []
    for lbl in orden:
        if lbl in agg:
            d = agg[lbl]
            d["rss_mb"] = round(d["rss_mb"], 1)
            d["cpu_percent"] = round(d["cpu_percent"], 1)
            out.append(d)
        else:
            out.append({"label": lbl, "pid": None, "rss_mb": 0.0, "cpu_percent": 0.0, "count": 0})
    return out


def _snapshot_sistema() -> dict[str, Any]:
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    try:
        load1, load5, load15 = psutil.getloadavg()
    except (AttributeError, OSError):
        load1 = load5 = load15 = 0.0

    return {
        "cpu_percent": round(psutil.cpu_percent(interval=None), 1),
        "cpu_count": psutil.cpu_count(),
        "load_avg": {"1m": round(load1, 2), "5m": round(load5, 2), "15m": round(load15, 2)},
        "memory": {
            "total_mb": round(mem.total / (1024 * 1024), 1),
            "used_mb": round(mem.used / (1024 * 1024), 1),
            "available_mb": round(mem.available / (1024 * 1024), 1),
            "percent": mem.percent,
        },
        "swap": {
            "total_mb": round(swap.total / (1024 * 1024), 1),
            "used_mb": round(swap.used / (1024 * 1024), 1),
            "percent": swap.percent,
        },
        "disk": {
            "total_gb": round(disk.total / (1024**3), 1),
            "used_gb": round(disk.used / (1024**3), 1),
            "percent": disk.percent,
        },
        "uptime_s": int(time.time() - psutil.boot_time()),
    }


def _take_snapshot() -> dict[str, Any]:
    return {
        "ts": int(time.time()),
        "system": _snapshot_sistema(),
        "processes": _snapshot_procesos(),
    }


# ── Background sampler (llamado desde lifespan en api/main.py) ────────────────
async def resources_sampler_loop(interval_s: int = 60):
    """Loop que toma un snapshot cada `interval_s` segundos y lo appendea al buffer."""
    # La primera llamada a cpu_percent(interval=None) devuelve 0 — la "cebamos".
    psutil.cpu_percent(interval=None)
    for p in psutil.process_iter():
        try:
            p.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Primer snapshot inmediato para que /history no esté vacío en el arranque.
    await asyncio.sleep(2)
    try:
        _history.append(_take_snapshot())
    except Exception:
        logger.exception("resources sampler: primer snapshot falló")

    while True:
        await asyncio.sleep(interval_s)
        try:
            _history.append(_take_snapshot())
        except Exception:
            logger.exception("resources sampler: snapshot falló")


# ── Endpoints ─────────────────────────────────────────────────────────────────
@router.get("/resources")
def resources_now():
    """Snapshot en vivo del sistema + procesos de interés."""
    return _take_snapshot()


@router.get("/resources/history")
def resources_history(limit: int = Query(default=60, ge=1, le=_HISTORY_MAXLEN)):
    """Últimos `limit` snapshots del buffer circular (min granularidad 1 min)."""
    data = list(_history)
    return {
        "samples": data[-limit:],
        "maxlen": _HISTORY_MAXLEN,
        "started_at": int(_started),
    }
