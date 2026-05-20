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
    ("Trading",     "TimeSales",            "timestamp",  "TimeSales (rofex)",                  300, _AR_TZ),
    ("Trading",     "MarketSnapshot",       "updated_at", "MarketSnapshot",                     120, UTC),
    ("Trading",     "PortfolioSnapshot",    "updated_at", "PortfolioSnapshot (live tenencia)",  120, UTC),
    ("Trading",     "ForwardsLive",         "updated_at", "ForwardsLive",                        60, UTC),
    ("Trading",     "BreakevensLive",       "updated_at", "BreakevensLive",                      60, UTC),
    ("Opciones",    "OptionsSnapshot",      "updated_at", "OptionsSnapshot",                    180, UTC),
    ("Trading",     "CedearsSnapshot",      "updated_at", "CedearsSnapshot",                     60, UTC),
    ("Trading",     "CaucionSnapshot",      "updated_at", "CaucionSnapshot",                     60, UTC),
    ("Valuaciones", "DolarSnapshot",        "updated_at", "DolarSnapshot",                       60, UTC),
    ("Trading",     "FuturosDLRSnapshot",   "updated_at", "FuturosDLRSnapshot",                  60, UTC),
    ("Trading",     "AgroSnapshot",         "updated_at", "AgroSnapshot",                        60, UTC),
    ("Trading",     "AgroOpcionesSnapshot", "updated_at", "AgroOpcionesSnapshot",                60, UTC),
]

_JOBS_STATUS = [
    ("Trading",     "CER",         "fecha",          "iso",      "CER (BCRA)",           2, "diario 20:00 UTC"),
    ("Trading",     "DOLAR",       "fecha",           "iso",      "DOLAR (BCRA)",         2, "diario 20:00 UTC"),
    ("Valuaciones", "AuM",         "fecha_snapshot",  "iso",      "AuM snapshot",         2, "diario 23:00 UTC L-V"),
    ("CashFlow",    "Movimientos", "fecha",           "ddmmyyyy", "CashFlow Movimientos",  2, "02:00 UTC mar-sáb"),
    ("CashFlow",    "Flujo",       "concertacion",    "iso",      "Flujo Contrapartes",    2, "diario 22:00 UTC L-V"),
]


# APIs externas que alimentan data — chequeamos el último dato escrito
# por el job consumidor. Tupla:
# (db, coll, field, tipo_ts, filtro, nombre, umbral_min, cadencia_label, solo_en_rueda)
#
# solo_en_rueda=True → fuera de la ventana 10-17 ART no se espera data
# nueva, último conocido se muestra como "fuera_ventana" (no stale).
_APIS_EXTERNAS = [
    ("Valuaciones", "DolarOficialLive",     "updated_at",        "datetime",
     None,                          "MAE UST$T (PC oficina)",   5,    "cada 30s en rueda",       True),
    ("Trading",     "RiesgoPais",           "fecha",              "iso",
     None,                          "argentinadatos (RP)",      36*60, "diario 12:00 UTC",       False),
    ("Trading",     "InflacionMensual",     "fecha",              "iso",
     None,                          "argentinadatos (IPC)",     36*60, "diario 12:00 UTC",       False),
    ("CashFlow",    "NegocioMovimientos",   "ingestado_en",       "datetime",
     None,                          "Aunesa (boletos)",         70,    "cada 60 min en rueda",   True),
    ("Market",      "Quotes",               "updated_at",         "datetime",
     None,                          "Yahoo (market_quotes)",    10,    "cada 1 min 13-21 UTC L-V", True),
    ("News",        "Headlines",            "fecha_publicacion",  "datetime",
     {"fuente": "finnhub"},         "Finnhub news",             60,    "*/30 min 12-23 UTC",     False),
    ("News",        "Headlines",            "fecha_publicacion",  "datetime",
     {"fuente": {"$ne": "finnhub"}},"RSS medios AR",            45,    "*/15 min 12-23 UTC",     False),
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

    def check_api(s):
        db_n, coll, field, tipo, filtro, nombre, umbral_min, cadencia, solo_rueda = s
        base_filtro = {field: {"$exists": True, "$nin": [None, ""]}}
        if filtro:
            base_filtro = {**base_filtro, **filtro}
        doc = _fetch_last(db_n, coll, field, base_filtro)
        if not doc:
            return {
                "nombre": nombre, "ultimo": None, "hace": "—",
                "cadencia": cadencia, "estado": "sin_datos",
            }
        ts = _parse_ts(doc.get(field), tipo)
        if not ts:
            return {
                "nombre": nombre, "ultimo": str(doc.get(field))[:19], "hace": "—",
                "cadencia": cadencia, "estado": "error_parse",
            }
        delta = (ahora.astimezone(UTC) - ts.astimezone(UTC)).total_seconds()
        delta_min = delta / 60

        if solo_rueda and not rueda:
            estado = "fuera_rueda"
        elif delta_min > umbral_min * 3:
            estado = "critico"
        elif delta_min > umbral_min:
            estado = "lento"
        else:
            estado = "ok"

        return {
            "nombre":   nombre,
            "ultimo":   ts.astimezone(_AR_TZ).strftime("%Y-%m-%d %H:%M"),
            "hace":     _fmt_delta(delta),
            "cadencia": cadencia,
            "umbral":   f"{umbral_min}m",
            "estado":   estado,
        }

    with ThreadPoolExecutor(max_workers=8) as ex:
        motores = list(ex.map(check_motor, _MOTORES))
        jobs    = list(ex.map(check_job,   _JOBS_STATUS))
        apis    = list(ex.map(check_api,   _APIS_EXTERNAS))

    return {
        "ahora_ar": ahora.strftime("%Y-%m-%d %H:%M:%S"),
        "en_rueda": rueda,
        "motores":  motores,
        "jobs":     jobs,
        "apis":     apis,
    }
