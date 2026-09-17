"""GET /api/manager/status — estado unificado de motores y jobs batch.

Frescura 100% SQL (decomiso Mongo): cada entrada declara (tabla, ts_expr[, where])
y se toma max((ts_expr)::timestamptz). La frescura fina de los crons vive en
/manager/jobs (manager.job_runs); esto es el semáforo de la data visible.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time

from fastapi import APIRouter

from api.routers.manager._common import _AR_TZ
from api.services import manager_infra_sql

router = APIRouter()


def _fmt_delta(s: float) -> str:
    s = int(s)
    if s < 0:     return "—"
    if s < 60:    return f"{s}s"
    if s < 3600:  return f"{s // 60}m {s % 60}s"
    if s < 86400: return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}d {(s % 86400) // 3600}h"


_fetch_last_sql = manager_infra_sql.ultimo_ts_sql   # frescura por tabla (best-effort)


_APERTURA_DEFAULT = time(10, 0)
_APERTURA_AGRO    = time(10, 30)   # MATBA abre 10:30 ART


def _es_rueda(ahora_ar: datetime, apertura: time = _APERTURA_DEFAULT) -> bool:
    return ahora_ar.weekday() < 5 and apertura <= ahora_ar.time() <= time(17, 5)


# Tupla: (nombre, umbral_s, apertura_ar, sql=(tabla, ts_expr[, where]))
# Tablas sin schema → search_path (core.postgres).
_MOTORES = [
    ("TimeSales (rofex)",                 300, _APERTURA_DEFAULT, ("timesales", "ts")),
    ("MarketSnapshot",                    120, _APERTURA_DEFAULT, ("market_snapshot", "updated_at")),
    ("PortfolioSnapshot (live tenencia)", 120, _APERTURA_DEFAULT, ("portfolio_snapshot", "updated_at")),
    ("ForwardsLive",                       60, _APERTURA_DEFAULT, ("mercado_hist", "data->>'updated_at'", "coleccion='ForwardsHistorico'")),
    ("BreakevensLive",                     60, _APERTURA_DEFAULT, ("mercado_hist", "data->>'updated_at'", "coleccion='BreakevensHistorico'")),
    ("OptionsSnapshot",                   180, _APERTURA_DEFAULT, ("options_snapshot", "updated_at")),
    # CedearsSnapshot (mercado.cedears_snapshot): frescura del motor via systemd /
    # skill /motor-status — no entra en este semáforo.
    ("CaucionSnapshot",                    60, _APERTURA_DEFAULT, ("caucion_snapshot", "data->>'updated_at'")),
    ("DolarSnapshot",                      60, _APERTURA_DEFAULT, ("dolar_snapshot", "ts")),
    ("FuturosDLRSnapshot",                 60, _APERTURA_DEFAULT, ("futuros_dlr_snapshot", "data->>'updated_at'")),
    ("AgroSnapshot",                       60, _APERTURA_AGRO,    ("agro_snapshot", "data->>'updated_at'")),
    ("AgroOpcionesSnapshot",               60, _APERTURA_AGRO,    ("agro_opciones_snapshot", "data->>'updated_at'")),
    # motor_ordenes solo escribe ER cuando hay actividad — el heartbeat singleton
    # (refrescado cada 30s) es la señal de vida. Arranca 10:30 ART (cron).
    ("MotorOrdenes (ER WS)",               90, _APERTURA_AGRO,    ("operaciones.motor_heartbeat", "updated_at")),
]

# Tupla: (nombre, umbral_dias, cadencia_label, sql=(tabla, ts_expr[, where]))
_JOBS_STATUS = [
    ("CER (BCRA)",           2, "diario 20:00 UTC",      ("macro.series_macro", "fecha", "serie = 'CER'")),
    ("DOLAR (BCRA)",         2, "diario 20:00 UTC",      ("macro.series_macro", "fecha", "serie = 'DOLAR'")),
    ("Tenencias (AuM)",      2, "diario 11:00 UTC L-V",  ("portafolio.tenencia", "fecha")),
    ("CashFlow Movimientos", 2, "02:00 UTC mar-sáb",     ("operaciones.movimientos", "to_date(fecha, 'DD/MM/YYYY')")),
    ("Flujo Contrapartes",   2, "diario 22:00 UTC L-V",  ("operaciones.operaciones", "concertacion")),
]

# APIs externas que alimentan data — se chequea el último dato escrito por el
# job consumidor. Tupla: (nombre, umbral_min, cadencia_label, solo_en_rueda, sql).
# solo_en_rueda=True → fuera de la ventana 10-17 ART no se espera data nueva,
# último conocido se muestra como "fuera_ventana" (no stale).
_APIS_EXTERNAS = [
    ("MAE UST$T (PC oficina)",   5,    "cada 30s en rueda",        True,
     ("valuaciones.dolar_oficial_live", "updated_at")),
    ("argentinadatos (RP)",      36*60, "diario 12:00 UTC",        False,
     ("macro.series_macro", "fecha", "serie = 'RiesgoPais'")),
    ("argentinadatos (IPC)",     36*60, "diario 12:00 UTC",        False,
     ("macro.series_macro", "fecha", "serie = 'InflacionMensual'")),
    ("Aunesa (boletos)",         70,    "cada 60 min en rueda",    True,
     ("negocio_movimientos", "ingestado_en")),
    ("Yahoo (market_quotes)",    10,    "cada 1 min 13-21 UTC L-V", True,
     ("home.market_quotes", "data->>'updated_at'")),
    ("Finnhub news",             60,    "*/30 min 12-23 UTC",      False,
     ("news_headlines", "fecha_publicacion", "fuente = 'finnhub'")),
    ("RSS medios AR",            45,    "*/15 min 12-23 UTC",      False,
     ("news_headlines", "fecha_publicacion", "fuente <> 'finnhub'")),
]


@router.get("/status")
def get_status():
    ahora = datetime.now(_AR_TZ)
    rueda = _es_rueda(ahora)

    def check_motor(s):
        nombre, umbral, apertura, sql = s
        ts = _fetch_last_sql(*sql)
        if not ts:
            return {"nombre": nombre, "ultima": None, "hace": "—", "umbral": umbral, "estado": "sin_datos"}
        delta = (ahora.astimezone(UTC) - ts.astimezone(UTC)).total_seconds()
        rueda_motor = _es_rueda(ahora, apertura)
        if not rueda_motor:       estado = "fuera_rueda"
        elif delta > umbral * 3:  estado = "critico"
        elif delta > umbral:      estado = "lento"
        else:                     estado = "ok"
        return {
            "nombre": nombre,
            "ultima": ts.astimezone(_AR_TZ).strftime("%H:%M:%S"),
            "hace":   _fmt_delta(delta),
            "umbral": umbral,
            "estado": estado,
        }

    def check_job(s):
        nombre, umbral_dias, freq, sql = s
        ts = _fetch_last_sql(*sql)
        if not ts:
            return {"nombre": nombre, "ultimo": None, "hace": "—", "frecuencia": freq, "estado": "sin_datos"}
        # Las 5 entradas son columnas `date` (el cast a timestamptz da medianoche
        # UTC) → comparar por fecha UTC, sin correrla a ART (restaría un día).
        fecha = ts.astimezone(UTC).date()
        delta_dias = (ahora.date() - fecha).days
        estado = "ok" if delta_dias <= 0 else ("atrasado" if delta_dias <= umbral_dias else "critico")
        return {
            "nombre":    nombre,
            "ultimo":    fecha.strftime("%Y-%m-%d"),
            "hace":      f"{delta_dias}d" if delta_dias > 0 else "hoy",
            "frecuencia": freq,
            "estado":    estado,
        }

    def check_api(s):
        nombre, umbral_min, cadencia, solo_rueda, sql = s
        ts = _fetch_last_sql(*sql)
        if ts is None:
            return {
                "nombre": nombre, "ultimo": None, "hace": "—",
                "cadencia": cadencia, "estado": "sin_datos",
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
