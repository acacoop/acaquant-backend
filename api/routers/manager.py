"""Manager API: diagnóstico de motores/jobs, backfills, changelog, latencia."""
import io
import os
import subprocess
import sys
import threading
import time as _time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date as _date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from bson import ObjectId
from fastapi import APIRouter, Body, File, Form, HTTPException, Path, Query, UploadFile
from pydantic import BaseModel

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
# ── Checks / validaciones ─────────────────────────────────────────────────────

@router.get("/checks/curvas-pendientes")
def check_curvas_pendientes():
    """Docs sin duration en TimeSales agrupados por ticker."""
    client = get_mongo_client_read()
    rows = list(client["Trading"]["TimeSales"].aggregate([
        {"$match": {"duration": {"$exists": False}}},
        {"$group": {"_id": "$ticker", "pendientes": {"$sum": 1}}},
        {"$sort": {"pendientes": -1}},
    ]))
    total = sum(r["pendientes"] for r in rows)
    return {"total": total, "ok": total == 0,
            "tickers": [{"ticker": r["_id"], "pendientes": r["pendientes"]} for r in rows]}


@router.get("/checks/forwards")
def check_forwards():
    """TEA disponible por instrumento en Curvas vs ForwardsLive."""
    client = get_mongo_client_read()
    db = client["Trading"]
    grupos: dict[str, list] = {}
    for d in db["Curvas"].find({}):
        grupos.setdefault(d.get("curva", "?"), []).append(d)

    all_tickers = [i["ticker"] for insts in grupos.values() for i in insts if i.get("ticker")]
    teas_all: dict = {}
    if all_tickers:
        for r in db["TimeSales"].aggregate([
            {"$match": {"ticker": {"$in": all_tickers}, "TEA": {"$exists": True}, "duration": {"$exists": True}}},
            {"$sort": {"timestamp": -1}},
            {"$group": {"_id": "$ticker", "TEA": {"$first": "$TEA"}, "duration": {"$first": "$duration"}, "ts": {"$first": "$timestamp"}}},
        ]):
            teas_all[r["_id"]] = r

    resultado = []
    for curva, instrumentos in sorted(grupos.items()):
        insts = sorted(instrumentos, key=lambda x: x.get("fecha_vencimiento") or "9999")
        tickers_curva = []
        for inst in insts:
            tk = inst.get("ticker", "?")
            datos = teas_all.get(tk)
            tickers_curva.append({
                "ticker":   inst.get("ticker_corto", "?"),
                "vto":      str(inst.get("fecha_vencimiento") or "?")[:10],
                "tea":      round(datos["TEA"] * 100, 4) if datos else None,
                "duration": round(datos["duration"], 4) if datos else None,
                "ultimo":   datos["ts"].strftime("%d/%m %H:%M") if datos and datos.get("ts") else None,
                "ok":       datos is not None,
            })
        live = db["ForwardsLive"].find_one({"curva": curva}, {"tickers": 1})
        live_tickers = live.get("tickers", []) if live else []
        expected = [i.get("ticker_corto") for i in insts if teas_all.get(i.get("ticker"))]
        resultado.append({"curva": curva, "tickers": tickers_curva,
                          "live_ok": live_tickers == expected,
                          "live_tickers": live_tickers, "expected_tickers": expected})
    return resultado


@router.get("/checks/cer")
def check_cer():
    """CER usado en el último trade enriquecido por bono CER."""
    from datetime import date, timedelta
    client = get_mongo_client_read()
    db = client["Trading"]
    curvas_cer = list(db["Curvas"].find({"curva": "cer"},
                                        {"ticker": 1, "ticker_corto": 1, "cer_emision": 1}))
    if not curvas_cer:
        return {"cer_reciente": None, "dias_habiles": 0, "instrumentos": []}

    cer_dict = {d["fecha"]: float(d["valor"]) for d in db["CER"].find({}, {"fecha": 1, "valor": 1})}
    dias_hab = sorted(d["fecha"] for d in db["DiasHabiles"].find({}, {"fecha": 1, "_id": 0}))
    cer_reciente = max(cer_dict.keys()) if cer_dict else None

    def _cer_en_fecha(fd):
        for i in range(7):
            k = (fd - timedelta(days=i)).isoformat()
            if k in cer_dict:
                return k, cer_dict[k]
        return None, None

    def _cer_liq(settle_str, n=10):
        idx = next((i for i, f in enumerate(dias_hab) if f <= settle_str), None)
        if idx is None or idx < n:
            return None, None
        return _cer_en_fecha(date.fromisoformat(dias_hab[idx - n]))

    def _next_habil(fd):
        s = fd.isoformat()
        return next((f for f in dias_hab if f > s), None)

    rows = []
    for inst in curvas_cer:
        doc = db["TimeSales"].find_one(
            {"ticker": inst["ticker"], "duration": {"$exists": True}}, sort=[("timestamp", -1)]
        )
        if not doc:
            rows.append({"ticker": inst.get("ticker_corto", "?"), "ok": False})
            continue
        ts = doc["timestamp"]
        fd = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
        settle = _next_habil(fd)
        cf, cv = _cer_liq(settle) if settle else (None, None)
        ce = inst.get("cer_emision")
        ratio = round(cv / ce, 6) if (cv and ce) else None
        rows.append({
            "ticker":       inst.get("ticker_corto", "?"),
            "ultimo_trade": ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)[:16],
            "settlement":   settle, "cer_fecha": cf,
            "cer_valor":    round(cv, 6) if cv else None,
            "cer_emision":  round(ce, 6) if ce else None,
            "ratio":        ratio,
            "paridad":      round(doc.get("paridad", 0), 2) if doc.get("paridad") else None,
            "ok":           ratio is not None,
        })
    return {"cer_reciente": cer_reciente, "dias_habiles": len(dias_hab), "instrumentos": rows}


@router.get("/checks/tasa-fija")
def check_tasa_fija():
    """Estado de instrumentos tasa_fija en AuM."""
    client = get_mongo_client_read()
    db_t = client["Trading"]
    db_v = client["Valuaciones"]
    curvas_tf = list(db_t["Curvas"].find({"curva": "tasa_fija"}, {"_id": 0, "ticker_corto": 1}))
    if not curvas_tf:
        return {"snapshot": None, "ok": 0, "sin_posicion": 0, "sin_assets": 0, "instrumentos": []}

    t2u: dict[str, list] = {}
    for a in db_v["Assets"].find({}, {"_id": 0, "TICKER": 1, "unidad": 1}):
        t2u.setdefault(a.get("TICKER", ""), []).append(a["unidad"])

    uf = db_v["AuM"].find_one(sort=[("fecha_snapshot", -1)], projection={"fecha_snapshot": 1})
    fm = uf["fecha_snapshot"] if uf else None
    con_pos = {d["unidad"] for d in
               db_v["AuM"].find({"fecha_snapshot": fm}, {"_id": 0, "unidad": 1, "valuacion": 1})
               if (d.get("valuacion") or 0) != 0} if fm else set()

    rows = []
    for c in sorted(curvas_tf, key=lambda x: x.get("ticker_corto", "")):
        tc = c["ticker_corto"]
        uns = t2u.get(tc, [])
        estado = "ok" if any(u in con_pos for u in uns) else ("sin_assets" if not uns else "sin_posicion")
        rows.append({"ticker": tc, "estado": estado})

    return {"snapshot": str(fm)[:10] if fm else None,
            "ok": sum(1 for r in rows if r["estado"] == "ok"),
            "sin_posicion": sum(1 for r in rows if r["estado"] == "sin_posicion"),
            "sin_assets": sum(1 for r in rows if r["estado"] == "sin_assets"),
            "instrumentos": rows}


@router.get("/checks/debug-forward")
def debug_forward(
    tc_a: str = Query(..., description="ticker_corto instrumento A"),
    tc_b: str = Query(..., description="ticker_corto instrumento B"),
):
    """Cálculo paso a paso de la tasa forward entre dos instrumentos."""
    client = get_mongo_client_read()
    db = client["Trading"]

    def _ultima_tea(tc: str):
        full = db["Curvas"].find_one({"ticker_corto": tc}, {"ticker": 1})
        if not full:
            return None, None, None
        doc = db["TimeSales"].find_one(
            {"ticker": full["ticker"], "TEA": {"$exists": True}, "duration": {"$exists": True}},
            sort=[("timestamp", -1)]
        )
        if not doc:
            return None, None, None
        return doc.get("TEA"), doc.get("duration"), doc.get("timestamp")

    tea_a, dur_a, ts_a = _ultima_tea(tc_a)
    tea_b, dur_b, ts_b = _ultima_tea(tc_b)

    base = {
        "tc_a": tc_a, "tea_a": tea_a, "duration_a": dur_a,
        "ts_a": ts_a.strftime("%d/%m %H:%M") if ts_a else None,
        "tc_b": tc_b, "tea_b": tea_b, "duration_b": dur_b,
        "ts_b": ts_b.strftime("%d/%m %H:%M") if ts_b else None,
    }

    if not all(x is not None for x in [tea_a, tea_b, dur_a, dur_b]):
        return {**base, "error": "Faltan TEA o Duration para uno o ambos tickers.", "pasos": [], "forward": None}

    if dur_a <= dur_b:
        ta, ra, na, tb, rb, nb = dur_a, tea_a, tc_a, dur_b, tea_b, tc_b
    else:
        ta, ra, na, tb, rb, nb = dur_b, tea_b, tc_b, dur_a, tea_a, tc_a

    dt = tb - ta
    if dt <= 0:
        return {**base, "error": "Δt ≤ 0, no se puede calcular la forward.", "pasos": [], "forward": None}

    num = (1 + rb) ** tb
    den = (1 + ra) ** ta
    fwd = (num / den) ** (1 / dt) - 1

    pasos = [
        {"paso": f"t corto ({na}) — duration",   "valor": f"{ta:.6f}"},
        {"paso": f"t largo ({nb}) — duration",   "valor": f"{tb:.6f}"},
        {"paso": "Δt",                            "valor": f"{dt:.6f}"},
        {"paso": f"(1+TEA_{nb})^t_largo",         "valor": f"{num:.8f}"},
        {"paso": f"(1+TEA_{na})^t_corto",         "valor": f"{den:.8f}"},
        {"paso": "Cociente num/den",              "valor": f"{num/den:.8f}"},
        {"paso": "Forward resultante",            "valor": f"{fwd*100:.4f}%"},
    ]
    return {**base, "forward": round(fwd * 100, 4), "error": None, "pasos": pasos}


@router.get("/checks/tickers-curvas")
def tickers_curvas():
    """Lista de ticker_corto disponibles en Trading.Curvas."""
    client = get_mongo_client_read()
    return sorted({
        d["ticker_corto"] for d in
        client["Trading"]["Curvas"].find({}, {"ticker_corto": 1})
        if d.get("ticker_corto")
    })


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


# ── Config de Opciones (vencimientos trackeados) ─────────────────────────────
@router.get("/options/expiries")
def get_options_expiries():
    """Lee Opciones.Metadata: vencimientos disponibles (publicados por el
    engine) y los activos (elegidos por el user en el Manager).

    Si `activos` está vacía → el engine hace auto-pick del próximo > hoy.
    """
    cfg = (
        get_mongo_client_read()["Opciones"]["Metadata"].find_one({"type": "config"}) or {}
    )
    disponibles = cfg.get("expiries_disponibles", []) or []
    activos     = cfg.get("expiries", []) or []
    actualizado = cfg.get("expiries_updated_at")
    if isinstance(actualizado, datetime):
        actualizado = (actualizado if actualizado.tzinfo else actualizado.replace(tzinfo=UTC)) \
            .astimezone(_AR_TZ).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "disponibles": disponibles,
        "activos":     activos,
        "auto_pick":   not activos,
        "actualizado": actualizado,
        "mapa_size":   cfg.get("mapa_size"),
    }


class _ExpiriesPayload(BaseModel):
    expiries: list[str] | None = None  # None o [] → auto-pick


@router.put("/options/expiries")
def put_options_expiries(payload: _ExpiriesPayload):
    """Persiste los vencimientos elegidos en Opciones.Metadata. El engine los
    toma en el próximo chequeo periódico (~5 min) y refresca su mapa."""
    valor = [e for e in (payload.expiries or []) if isinstance(e, str) and len(e) == 8]
    get_mongo_client()["Opciones"]["Metadata"].update_one(
        {"type": "config"},
        {"$set": {"expiries": valor, "expiries_updated_by_ui": datetime.now(UTC)}},
        upsert=True,
    )
    return {"ok": True, "expiries": valor, "auto_pick": not valor}


# ── Historial de runs de jobs (Manager.JobRuns) ──────────────────────────────
@router.get("/jobs/history")
def get_jobs_history(
    tipo: str | None = Query(None, description="Filtrar por tipo de job (carteras, aum, ...)"),
    status: str | None = Query(None, description="ok | partial | error"),
    desde: str | None = Query(None, description="ISO datetime o YYYY-MM-DD (UTC)"),
    hasta: str | None = Query(None, description="ISO datetime o YYYY-MM-DD (UTC)"),
    limit: int = Query(100, le=500),
):
    """Últimas corridas registradas en Manager.JobRuns. La colección tiene
    TTL 60d definido en scripts/crear_indices.py."""
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


# ── Asistente IA: observabilidad de /api/chat ────────────────────────────────
# Precios Gemini 2.5 Flash (free tier = 0; los valores son para estimar costo
# cuando se active billing). USD por token.
_GEMINI_FLASH_PRICE_IN  = 0.30 / 1_000_000   # $0.30 / M input tokens
_GEMINI_FLASH_PRICE_OUT = 2.50 / 1_000_000   # $2.50 / M output tokens


def _asistente_coll():
    return get_mongo_client_read()["Manager"]["AsistenteLogs"]


@router.get("/asistente/stats")
def asistente_stats(horas: int = Query(24, ge=1, le=720)):
    """Métricas agregadas del asistente en las últimas `horas` horas."""
    desde = datetime.now(UTC) - timedelta(hours=horas)
    docs = list(_asistente_coll().find({"ts": {"$gte": desde}}, {"_id": 0}))

    total = len(docs)
    ok        = sum(1 for d in docs if d.get("estado") == "ok")
    errores   = sum(1 for d in docs if d.get("estado") == "error")
    truncated = sum(1 for d in docs if d.get("estado") == "truncated")

    usages = [d.get("usage") or {} for d in docs if d.get("usage")]
    tokens_in  = sum(u.get("promptTokenCount", 0) or 0 for u in usages)
    tokens_out = sum(u.get("candidatesTokenCount", 0) or 0 for u in usages)

    elapsed = [d.get("elapsed_s") for d in docs if isinstance(d.get("elapsed_s"), (int, float))]
    elapsed.sort()
    avg_s = round(sum(elapsed) / len(elapsed), 2) if elapsed else 0.0
    p95_s = round(elapsed[int(len(elapsed) * 0.95)], 2) if len(elapsed) >= 20 else (
        round(max(elapsed), 2) if elapsed else 0.0
    )

    costo_usd = round(
        tokens_in * _GEMINI_FLASH_PRICE_IN + tokens_out * _GEMINI_FLASH_PRICE_OUT,
        4,
    )

    return {
        "periodo_horas": horas,
        "conversaciones_total": total,
        "conversaciones_ok": ok,
        "conversaciones_error": errores,
        "conversaciones_truncated": truncated,
        "pct_error": round(errores / total * 100, 2) if total else 0,
        "pct_truncated": round(truncated / total * 100, 2) if total else 0,
        "tokens_input": tokens_in,
        "tokens_output": tokens_out,
        "tokens_total": tokens_in + tokens_out,
        "latencia_avg_s": avg_s,
        "latencia_p95_s": p95_s,
        "costo_estimado_usd": costo_usd,
    }


@router.get("/asistente/logs")
def asistente_logs(
    limit: int = Query(50, ge=1, le=500),
    estado: str = Query("all", description="all | ok | error | truncated"),
    horas: int = Query(24, ge=1, le=720),
):
    """Últimas N conversaciones, ordenadas desc por timestamp."""
    desde = datetime.now(UTC) - timedelta(hours=horas)
    filtro: dict = {"ts": {"$gte": desde}}
    if estado in ("ok", "error", "truncated"):
        filtro["estado"] = estado

    cur = _asistente_coll().find(filtro, {"_id": 0}).sort("ts", -1).limit(limit)
    docs = []
    for d in cur:
        # Serialización: pymongo devuelve datetime naive (BSON siempre es UTC
        # internamente). Le anotamos tzinfo=UTC para que el ISO output lleve
        # +00:00 y el browser no lo interprete como local.
        v = d.get("ts")
        if isinstance(v, datetime):
            d["ts"] = (v if v.tzinfo else v.replace(tzinfo=UTC)).isoformat()
        docs.append(d)
    return docs


@router.get("/asistente/timeseries")
def asistente_timeseries(horas: int = Query(24, ge=1, le=720)):
    """Serie por hora: conversaciones, tokens, errores."""
    desde = datetime.now(UTC) - timedelta(hours=horas)
    pipeline = [
        {"$match": {"ts": {"$gte": desde}}},
        {"$group": {
            "_id": {
                "y": {"$year": "$ts"},
                "m": {"$month": "$ts"},
                "d": {"$dayOfMonth": "$ts"},
                "h": {"$hour": "$ts"},
            },
            "count": {"$sum": 1},
            "tokens": {"$sum": {"$ifNull": ["$usage.totalTokenCount", 0]}},
            "errors": {"$sum": {"$cond": [{"$eq": ["$estado", "error"]}, 1, 0]}},
        }},
        {"$sort": {"_id": 1}},
    ]
    rows = list(_asistente_coll().aggregate(pipeline))
    return [
        {
            "bucket": datetime(r["_id"]["y"], r["_id"]["m"], r["_id"]["d"], r["_id"]["h"], tzinfo=UTC).isoformat(),
            "count": r["count"],
            "tokens": r["tokens"] or 0,
            "errors": r["errors"],
        }
        for r in rows
    ]


# ── INTEL: ingesta de reportes / extracción de variables macro ───────────────

def _intel_coll():
    # Writable porque hay inserts/updates/deletes.
    return get_mongo_client()["Manager"]["IntelDocs"]


def _oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"id inválido: {e}") from e


def _serialize_intel_doc(d: dict) -> dict:
    d = dict(d)
    if "_id" in d:
        d["id"] = str(d.pop("_id"))
    for k in ("fecha", "created_at", "updated_at", "confirmed_at"):
        if isinstance(d.get(k), datetime):
            d[k] = d[k].astimezone(UTC).isoformat()
        elif isinstance(d.get(k), _date):
            d[k] = d[k].isoformat()
    return d


def _extract_pdf_text(content: bytes) -> str:
    """Parsea un PDF en bytes y devuelve el texto concatenado."""
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail="pypdf no instalado en el server. Correr pip install pypdf.",
        ) from e
    try:
        reader = PdfReader(io.BytesIO(content))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n\n".join(parts).strip()
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"no pude leer el PDF: {e}",
        ) from e


class IntelSaveRequest(BaseModel):
    fuente: str
    fecha: str | None = None  # YYYY-MM-DD
    titulo: str | None = None
    raw_text: str
    extracted: dict


@router.post("/intel/extract")
async def intel_extract(
    fuente: str = Form(...),
    fecha: str | None = Form(None),
    titulo: str | None = Form(None),
    texto: str | None = Form(None),
    pdf: UploadFile | None = File(None),
):
    """Extrae variables estructuradas del texto o PDF. NO persiste todavía;
    devuelve el preview para que el usuario confirme/edite y después guarde."""
    from api.agent.intel_extraction import ExtractionError, extract_intel

    raw_text = (texto or "").strip()
    if pdf is not None:
        content = await pdf.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="PDF > 10 MB")
        pdf_text = _extract_pdf_text(content)
        if raw_text and pdf_text:
            raw_text = pdf_text + "\n\n---\n\n" + raw_text
        elif pdf_text:
            raw_text = pdf_text

    if not raw_text:
        raise HTTPException(status_code=400, detail="no se recibió texto ni PDF con contenido")

    try:
        extracted = extract_intel(raw_text)
    except ExtractionError as e:
        raise HTTPException(status_code=502, detail=f"error extrayendo: {e}") from e

    return {
        "fuente": fuente,
        "fecha": fecha or datetime.now(_AR_TZ).date().isoformat(),
        "titulo": titulo or "",
        "raw_text": raw_text,
        "extracted": extracted,
        "chars": len(raw_text),
    }


@router.post("/intel/save")
def intel_save(req: IntelSaveRequest):
    """Guarda un IntelDoc confirmado en Manager.IntelDocs."""
    doc = {
        "fuente": req.fuente.strip(),
        "fecha": req.fecha or datetime.now(_AR_TZ).date().isoformat(),
        "titulo": (req.titulo or "").strip(),
        "raw_text": req.raw_text,
        "extracted": req.extracted,
        "confirmed": True,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "confirmed_at": datetime.now(UTC),
    }
    result = _intel_coll().insert_one(doc)
    doc["_id"] = result.inserted_id
    return _serialize_intel_doc(doc)


@router.get("/intel")
def intel_list(
    limit: int = Query(50, ge=1, le=200),
    fuente: str | None = Query(None),
):
    """Lista los IntelDocs ordenados por fecha desc."""
    filtro: dict = {}
    if fuente:
        filtro["fuente"] = fuente
    cur = (
        _intel_coll()
        .find(filtro)
        .sort([("fecha", -1), ("created_at", -1)])
        .limit(limit)
    )
    return [_serialize_intel_doc(d) for d in cur]


@router.get("/intel/latest")
def intel_latest():
    """Último IntelDoc confirmado, usado por context.py para inyectar en el prompt."""
    doc = _intel_coll().find_one(
        {"confirmed": True},
        sort=[("fecha", -1), ("created_at", -1)],
    )
    if not doc:
        return {}
    return _serialize_intel_doc(doc)


@router.get("/intel/{intel_id}")
def intel_get(intel_id: str):
    doc = _intel_coll().find_one({"_id": _oid(intel_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="IntelDoc no encontrado")
    return _serialize_intel_doc(doc)


class IntelPatchRequest(BaseModel):
    fuente: str | None = None
    fecha: str | None = None
    titulo: str | None = None
    extracted: dict | None = None


@router.patch("/intel/{intel_id}")
def intel_patch(intel_id: str, req: IntelPatchRequest):
    update: dict = {"updated_at": datetime.now(UTC)}
    if req.fuente is not None:     update["fuente"] = req.fuente.strip()
    if req.fecha is not None:      update["fecha"] = req.fecha
    if req.titulo is not None:     update["titulo"] = req.titulo.strip()
    if req.extracted is not None:  update["extracted"] = req.extracted

    result = _intel_coll().update_one({"_id": _oid(intel_id)}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="IntelDoc no encontrado")
    doc = _intel_coll().find_one({"_id": _oid(intel_id)})
    return _serialize_intel_doc(doc) if doc else {}


@router.delete("/intel/{intel_id}")
def intel_delete(intel_id: str):
    result = _intel_coll().delete_one({"_id": _oid(intel_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="IntelDoc no encontrado")
    return {"ok": True}


@router.get("/asistente/tools-ranking")
def asistente_tools_ranking(horas: int = Query(24, ge=1, le=720)):
    """Ranking de tools invocadas + tasa de éxito/fallo."""
    desde = datetime.now(UTC) - timedelta(hours=horas)
    pipeline = [
        {"$match": {"ts": {"$gte": desde}, "tool_calls": {"$exists": True, "$ne": []}}},
        {"$unwind": "$tool_calls"},
        {"$group": {
            "_id": "$tool_calls.name",
            "calls": {"$sum": 1},
            "ok":    {"$sum": {"$cond": [{"$eq": ["$tool_calls.ok", True]}, 1, 0]}},
            "fail":  {"$sum": {"$cond": [{"$eq": ["$tool_calls.ok", False]}, 1, 0]}},
        }},
        {"$sort": {"calls": -1}},
    ]
    rows = list(_asistente_coll().aggregate(pipeline))
    return [
        {"tool": r["_id"], "calls": r["calls"], "ok": r["ok"], "fail": r["fail"]}
        for r in rows
    ]
