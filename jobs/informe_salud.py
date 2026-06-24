"""informe_salud.py — Informe de salud de ACAQuant (health report a Telegram).

Corre cada hora en rueda + una vez de noche. Junta métricas de salud del sistema,
las PERSISTE estructuradas en Manager.HealthReports (para que agentes futuros lean
la historia) y manda un resumen por Telegram. El mensaje es solo el render del
snapshot estructurado.

Secciones:
  1. VEREDICTO     — 🟢/🟡/🔴 global + headline.
  2. MOTORES       — frescura de cada snapshot live (updated_at más nuevo vs cadencia
                     esperada). En rueda, stale = caído; fuera de rueda no alarma.
  3. BASES         — crecimiento por colección: count (O(1), sin scan) + Δ vs informe
                     anterior. "Se están llenando" = delta positivo donde corresponde.
  4. JOBS          — Manager.JobRuns última hora: ok/partial/error + fallas + dailies
                     vencidos.
  5. SQL SYNC      — último run de sync_postgres (edad + estado).
  6. MONGO         — ping rw/ro (latencia).
  7. PROBLEMAS     — consolidado de anomalías.

Diseño (lead tech):
  - estimated_document_count es O(1) (metadata) → NUNCA escanea. Counts por rango de
    timestamp en colecciones grandes serían COLLSCAN (REGLA #4) → no se usan; el
    crecimiento sale del delta de counts entre informes.
  - find_one(sort=updated_at desc) solo en snapshots CHICOS (cientos/miles de docs).
  - Defensivo: cada métrica en try/except; una colección inexistente → "sin datos",
    no rompe el informe.

Uso:
    python -m jobs.informe_salud            # arma, persiste y manda a Telegram
    python -m jobs.informe_salud --dry      # imprime, NO persiste ni manda
    python -m jobs.informe_salud --no-telegram
"""
from __future__ import annotations

import sys
import time
from datetime import UTC, datetime, timedelta

from core.mongo import get_mongo_client

# ── Config ───────────────────────────────────────────────────────────────────

# Motores live: (label, db, colección, campo_ts, cadencia_seg_esperada).
# Colecciones verificadas (corrida de diag_frescura_snapshots + crontab). Si alguna
# aparece "sin datos" es que el nombre no coincide → ajustar acá.
MOTORES = [
    ("rofex/curvas", "Trading",  "MarketSnapshot",       "updated_at", 60),
    # cedears: CedearsSnapshot migrada a SQL (mercado.cedears_snapshot) 2026-06-24 —
    # sacada de este check Mongo-only. Frescura del motor: systemd / skill /motor-status.
    ("opciones",     "Opciones", "OptionsSnapshot",      "updated_at", 60),
    ("agro",         "Trading",  "AgroSnapshot",         "updated_at", 60),
    ("agro_opc",     "Trading",  "AgroOpcionesSnapshot", "updated_at", 60),
    ("futuros_dlr",  "Trading",  "FuturosDLRSnapshot",   "updated_at", 10),
    ("caucion",      "Trading",  "CaucionSnapshot",      "updated_at", 30),
]

# Bases a trackear crecimiento: (label, db, colección). Count O(1) + Δ vs anterior.
BASES = [
    ("ops",          "CashFlow",    "Operaciones"),
    ("negocio_mov",  "CashFlow",    "NegocioMovimientos"),
    ("aum",          "Valuaciones", "AuM"),
    ("timesales",    "Trading",     "TimeSales"),
    ("cedears_ts",   "Trading",     "CedearsTimeSales"),
    ("job_runs",     "Manager",     "JobRuns"),
]

# Jobs esperados → si el último corrió hace > umbral (horas), vencido. OJO: el
# `tipo` es el de JobRunLogger (nombre del job), NO la etiqueta del cron — el cron
# `negocio_chain` loguea como `negocio_movimientos`, etc.
DAILIES = [
    ("aum", 27), ("sync_postgres", 3), ("bcra", 27), ("argentina_datos", 27),
    ("snapshot_cierre", 27), ("operaciones_informes", 3), ("negocio_movimientos", 3),
]


def _ahora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _en_rueda(now: datetime) -> bool:
    """L-V 13-20 UTC = horario de motores de mercado."""
    return now.weekday() < 5 and 13 <= now.hour < 20


def _edad_seg(ts) -> float | None:
    if not isinstance(ts, datetime):
        return None
    base = ts.replace(tzinfo=None) if ts.tzinfo else ts
    return (_ahora() - base).total_seconds()


# ── Secciones ────────────────────────────────────────────────────────────────

def _seccion_motores(cli, en_rueda: bool) -> dict:
    out = {"items": [], "stale": 0, "muertos": 0}
    for label, db, coll, ts_field, cadencia in MOTORES:
        item = {"motor": label, "coll": f"{db}.{coll}", "edad_s": None, "estado": "sin_datos"}
        try:
            doc = cli[db][coll].find_one({ts_field: {"$ne": None}}, {ts_field: 1},
                                         sort=[(ts_field, -1)])
            if doc:
                edad = _edad_seg(doc.get(ts_field))
                item["edad_s"] = round(edad) if edad is not None else None
                if edad is None:
                    item["estado"] = "sin_ts"
                elif edad <= cadencia * 5:
                    item["estado"] = "fresco"
                elif en_rueda:
                    item["estado"] = "muerto" if edad > 900 else "stale"
                    if item["estado"] == "muerto":
                        out["muertos"] += 1
                    else:
                        out["stale"] += 1
                else:
                    item["estado"] = "off_rueda"
        except Exception as e:
            item["estado"] = f"error:{type(e).__name__}"
        out["items"].append(item)
    return out


def _seccion_bases(cli, prev: dict | None) -> dict:
    prev_counts = {}
    if prev:
        for b in (prev.get("bases") or {}).get("items", []):
            prev_counts[b["label"]] = b.get("count")
    items = []
    for label, db, coll in BASES:
        entry = {"label": label, "coll": f"{db}.{coll}", "count": None, "delta": None}
        try:
            entry["count"] = cli[db][coll].estimated_document_count()
            p = prev_counts.get(label)
            if isinstance(p, int) and isinstance(entry["count"], int):
                entry["delta"] = entry["count"] - p
        except Exception as e:
            entry["count"] = f"error:{type(e).__name__}"
        items.append(entry)
    return {"items": items}


def _seccion_jobs(cli) -> dict:
    col = cli["Manager"]["JobRuns"]
    desde = _ahora() - timedelta(hours=1)
    ok = partial = error = 0
    fallas = []
    try:
        for d in col.find({"started_at": {"$gte": desde}},
                          {"tipo": 1, "status": 1, "errors": 1, "elapsed_s": 1}):
            st = d.get("status")
            if st == "ok":
                ok += 1
            elif st == "partial":
                partial += 1
                fallas.append({"tipo": d.get("tipo"), "status": "partial",
                               "err": (d.get("errors") or [""])[0][:120]})
            else:
                error += 1
                fallas.append({"tipo": d.get("tipo"), "status": st,
                               "err": (d.get("errors") or [""])[0][:120]})
    except Exception as e:
        return {"ok": ok, "partial": partial, "error": error, "fallas": [],
                "vencidos": [], "err_seccion": f"{type(e).__name__}"}

    # Último run por tipo en UNA aggregation (antes: un find_one por DAILY → N+1).
    vencidos = []
    try:
        ultimo_por_tipo = {
            d["_id"]: d
            for d in col.aggregate([
                {"$match": {"tipo": {"$in": [t for t, _ in DAILIES]}}},
                {"$sort": {"started_at": -1}},
                {"$group": {
                    "_id": "$tipo",
                    "finished_at": {"$first": "$finished_at"},
                    "started_at": {"$first": "$started_at"},
                }},
            ])
        }
    except Exception:
        ultimo_por_tipo = {}
    for tipo, horas in DAILIES:
        last = ultimo_por_tipo.get(tipo)
        if not last:
            vencidos.append({"tipo": tipo, "horas": None})
            continue
        ref = last.get("finished_at") or last.get("started_at")
        edad = _edad_seg(ref)
        if edad is not None and edad / 3600 > horas:
            vencidos.append({"tipo": tipo, "horas": round(edad / 3600, 1)})
    return {"ok": ok, "partial": partial, "error": error, "fallas": fallas, "vencidos": vencidos}


def _seccion_sql(cli) -> dict:
    try:
        last = cli["Manager"]["JobRuns"].find_one(
            {"tipo": "sync_postgres"}, {"finished_at": 1, "started_at": 1, "status": 1},
            sort=[("started_at", -1)])
        if not last:
            return {"estado": "sin_runs", "edad_min": None}
        ref = last.get("finished_at") or last.get("started_at")
        edad = _edad_seg(ref)
        return {"estado": last.get("status"),
                "edad_min": round(edad / 60, 1) if edad is not None else None}
    except Exception as e:
        return {"estado": f"error:{type(e).__name__}", "edad_min": None}


def _seccion_mongo(cli) -> dict:
    out = {}
    try:
        t0 = time.perf_counter()
        cli.admin.command("ping")
        out["rw_ms"] = round((time.perf_counter() - t0) * 1000)
    except Exception as e:
        out["rw_ms"] = None
        out["rw_err"] = type(e).__name__
    try:
        from core.mongo import get_mongo_client_read
        ro = get_mongo_client_read()
        t0 = time.perf_counter()
        ro.admin.command("ping")
        out["ro_ms"] = round((time.perf_counter() - t0) * 1000)
    except Exception:
        out["ro_ms"] = None
    return out


# ── Veredicto + problemas ────────────────────────────────────────────────────

def _consolidar(rep: dict) -> tuple[str, list[str]]:
    problemas = []
    mot = rep["motores"]
    if mot["muertos"]:
        problemas.append(f"{mot['muertos']} motor(es) MUERTO(s) en rueda")
    if mot["stale"]:
        problemas.append(f"{mot['stale']} motor(es) stale en rueda")
    jb = rep["jobs"]
    if jb.get("error"):
        problemas.append(f"{jb['error']} job(s) con ERROR en la última hora")
    if jb.get("vencidos"):
        problemas.append("dailies vencidos: " + ", ".join(v["tipo"] for v in jb["vencidos"]))
    sq = rep["sql_sync"]
    if sq.get("estado") not in ("ok", None) or (sq.get("edad_min") or 0) > 60:
        problemas.append(f"sync SQL: {sq.get('estado')} (hace {sq.get('edad_min')} min)")
    if rep["mongo"].get("rw_ms") is None:
        problemas.append("Mongo rw no responde al ping")

    if mot["muertos"] or jb.get("error") or rep["mongo"].get("rw_ms") is None:
        return "🔴", problemas
    if problemas:
        return "🟡", problemas
    return "🟢", problemas


# ── Construcción + render + persistencia ─────────────────────────────────────

def construir_informe(cli) -> dict:
    now = _ahora()
    en_rueda = _en_rueda(now)
    prev = None
    try:
        prev = cli["Manager"]["HealthReports"].find_one({}, sort=[("ts", -1)])
    except Exception:
        pass

    rep: dict = {
        "ts": now,
        "en_rueda": en_rueda,
        "motores": _seccion_motores(cli, en_rueda),
        "bases": _seccion_bases(cli, prev),
        "jobs": _seccion_jobs(cli),
        "sql_sync": _seccion_sql(cli),
        "mongo": _seccion_mongo(cli),
    }
    veredicto, problemas = _consolidar(rep)
    rep["veredicto"] = veredicto
    rep["problemas"] = problemas
    return rep


def _fmt_delta(d) -> str:
    if not isinstance(d, int):
        return ""
    if d == 0:
        return " (=)"
    return f" ({'+' if d > 0 else ''}{d:,})".replace(",", ".")


def _explicar(rep: dict) -> list[str]:
    """Traduce cada problema detectado a lenguaje ejecutivo (qué pasó + qué
    hacer). REGLA #3: el informe llega con la explicación, no solo el síntoma."""
    out: list[str] = []
    mot = rep["motores"]
    jb = rep["jobs"]
    fallas_txt = " ".join(str(f.get("err", "")) for f in jb.get("fallas", []))

    if mot.get("muertos"):
        out.append("Motor(es) de mercado MUERTOS: dejaron de actualizar precios en "
                   "rueda. Grave — las vistas quedan con el dato viejo. Revisá el "
                   "systemd del motor en el Droplet.")
    if mot.get("stale"):
        out.append("Motor(es) STALE: actualizan pero atrasados. Suele ser mercado "
                   "quieto o feed lento; vigilá que no pasen a muerto.")
    if jb.get("error") and "PoolTimeout" in fallas_txt:
        out.append("Job con ERROR por PoolTimeout: el job no consiguió una conexión "
                   "libre a Postgres (Supabase) en 8s y abortó. NO es que el dato esté "
                   "mal — no pudo ni arrancar. Pasa en hora pico (rueda): hay pocas "
                   "conexiones y están todas ocupadas (API + sync + otros jobs). Se "
                   "arregla con más conexiones (upgrade del plan Supabase) o escalonando "
                   "los jobs para que no choquen. Reintenta solo en la próxima corrida.")
    elif jb.get("error"):
        out.append("Job con ERROR: un proceso batch falló. Detalle en Manager.JobRuns. "
                   "Si es transitorio, reintenta en la próxima corrida.")
    if jb.get("vencidos"):
        out.append("Daily(s) vencido(s): un proceso que debía correr hoy no dejó "
                   "registro en plazo. Causas típicas: cron caído, job renombrado, o un "
                   "check viejo apuntando a un job que ya no existe.")
    sq = rep["sql_sync"]
    if sq.get("estado") not in ("ok", None) or (sq.get("edad_min") or 0) > 60:
        out.append("Sync SQL atrasado: la copia Mongo→Postgres no corre hace rato. Las "
                   "vistas que leen SQL pueden mostrar datos viejos hasta el próximo sync.")
    if rep["mongo"].get("rw_ms") is None:
        out.append("Mongo no responde: la base principal (la que opera) no contesta el "
                   "ping. Es lo MÁS grave — atendé esto antes que nada.")
    return out


def render_telegram(rep: dict) -> str:
    now = rep["ts"]
    rueda = "rueda" if rep["en_rueda"] else "fuera de rueda"
    lines = [f"{rep['veredicto']} *Informe ACAQuant* — {now:%Y-%m-%d %H:%M} UTC ({rueda})"]

    if rep["problemas"]:
        lines.append("")
        lines.append("*⚠️ Problemas:*")
        for p in rep["problemas"]:
            lines.append(f"  • {p}")
        explic = _explicar(rep)
        if explic:
            lines.append("")
            lines.append("*🧭 Qué significa (criollo):*")
            for e in explic:
                lines.append(f"  • {e}")

    # Motores
    lines.append("")
    lines.append("*Motores:*")
    icon = {"fresco": "🟢", "stale": "🟡", "muerto": "🔴", "off_rueda": "💤",
            "sin_datos": "⚪", "sin_ts": "⚪"}
    for m in rep["motores"]["items"]:
        e = m["edad_s"]
        edad = f"{e}s" if isinstance(e, int) and e < 120 else (f"{round(e/60)}m" if isinstance(e, int) else "—")
        lines.append(f"  {icon.get(m['estado'], '⚪')} {m['motor']}: {edad}")

    # Bases
    lines.append("")
    lines.append("*Bases (Δ vs anterior):*")
    for b in rep["bases"]["items"]:
        c = b["count"]
        cstr = f"{c:,}".replace(",", ".") if isinstance(c, int) else str(c)
        lines.append(f"  • {b['label']}: {cstr}{_fmt_delta(b['delta'])}")

    # Jobs
    jb = rep["jobs"]
    lines.append("")
    lines.append(f"*Jobs (1h):* {jb.get('ok', 0)} ok · {jb.get('partial', 0)} partial · {jb.get('error', 0)} error")
    for f in jb.get("fallas", [])[:5]:
        lines.append(f"  ✗ {f['tipo']} [{f['status']}]: {f['err']}")
    if jb.get("vencidos"):
        lines.append("  ⏰ vencidos: " + ", ".join(f"{v['tipo']}({v['horas']}h)" for v in jb["vencidos"]))

    # SQL + Mongo
    sq = rep["sql_sync"]
    lines.append("")
    lines.append(f"*SQL sync:* {sq.get('estado')} (hace {sq.get('edad_min')} min)")
    mg = rep["mongo"]
    lines.append(f"*Mongo:* rw {mg.get('rw_ms')}ms · ro {mg.get('ro_ms')}ms")

    return "\n".join(lines)


def main() -> int:
    dry = "--dry" in sys.argv
    no_tg = "--no-telegram" in sys.argv
    cli = get_mongo_client()

    from core.job_runs import JobRunLogger
    with JobRunLogger("informe_salud") as jr:
        rep = construir_informe(cli)
        msg = render_telegram(rep)
        jr.set_stat("veredicto", rep["veredicto"])
        jr.set_stat("n_problemas", len(rep["problemas"]))

        if dry:
            print(msg)
            print("\n[--dry: no se persiste ni se manda]")
            return 0

        # Persistir el snapshot estructurado (TTL 45 días).
        try:
            col = cli["Manager"]["HealthReports"]
            col.create_index([("ts", -1)], name="ts_ttl",
                             expireAfterSeconds=45 * 24 * 3600)
            col.insert_one(rep)
        except Exception as e:
            jr.error(f"persist HealthReports: {type(e).__name__}: {e}")

        if not no_tg:
            from core.notify import send_telegram
            send_telegram(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
