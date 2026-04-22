"""GET /api/manager/status — estado unificado de motores y jobs batch."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time

from fastapi import APIRouter

from api.routers.manager._common import _AR_TZ
from core.mongo import get_mongo_client_read

router = APIRouter()


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
