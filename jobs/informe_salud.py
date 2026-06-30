"""informe_salud.py — Informe de salud de ACAQuant (health report a Telegram).

Corre cada hora en rueda + una vez de noche. Junta métricas de salud del sistema,
las PERSISTE estructuradas en SQL `health_reports` (para que agentes futuros lean
la historia) y manda un resumen por Telegram. El mensaje es solo el render del
snapshot estructurado.

Secciones (SQL-only — decomiso Mongo):
  1. VEREDICTO     — 🟢/🟡/🔴 global + headline.
  2. MOTORES       — frescura de cada snapshot live desde Postgres (max(updated_at) vs
                     cadencia esperada). En rueda, stale = caído; fuera de rueda no alarma.
  3. JOBS          — manager.job_runs última hora: ok/partial/error + fallas + dailies
                     vencidos.
  4. SQL SYNC      — último run de sync_postgres (edad + estado).
  5. PROBLEMAS     — consolidado de anomalías.

Diseño (lead tech):
  - Frescura de motores = max(ts)::timestamptz por tabla SQL (índice por ts) → barato.
  - Defensivo: cada métrica en try/except; si Postgres no responde → "sin datos",
    no rompe el informe.

Uso:
    python -m jobs.informe_salud            # arma, persiste y manda a Telegram
    python -m jobs.informe_salud --dry      # imprime, NO persiste ni manda
    python -m jobs.informe_salud --no-telegram
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

# ── Config ───────────────────────────────────────────────────────────────────

# Motores live: (label, cadencia_seg_esperada, (tabla_sql, ts_expr[, where])).
# Frescura desde Postgres (decomiso Mongo, SQL-only). Para los snapshots SQL el
# updated_at fresco vive en data->>'updated_at' (la columna updated_at queda con el
# now() del primer insert).
MOTORES = [
    ("rofex/curvas", 60, ("market_snapshot", "updated_at")),
    ("opciones",     60, ("options_snapshot", "updated_at")),
    ("agro",         60, ("agro_snapshot", "data->>'updated_at'")),
    ("agro_opc",     60, ("agro_opciones_snapshot", "data->>'updated_at'")),
    ("futuros_dlr",  10, ("futuros_dlr_snapshot", "data->>'updated_at'")),
    ("caucion",      30, ("caucion_snapshot", "data->>'updated_at'")),
]

# Jobs esperados → si el último corrió hace > umbral (horas), vencido. OJO: el
# `tipo` es el de JobRunLogger (nombre del job), NO la etiqueta del cron — el cron
# `negocio_chain` loguea como `negocio_movimientos`, etc.
# `aum` y `sync_postgres` se retiraron (decomiso Mongo 2026-06-29: el AuM lo escribe
# portafolio_backfill y no hay sync Mongo→SQL) → ya no se esperan (daban falsa alarma).
DAILIES = [
    ("bcra", 27), ("argentina_datos", 27), ("portafolio_backfill", 27),
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


def _frescura_sql(tabla: str, ts_expr: str, where: str | None = None):
    """Frescura desde Postgres (decomiso Mongo): max(ts_expr::timestamptz) de la
    tabla. Tablas sin schema → search_path (core.postgres). Devuelve datetime
    aware (UTC) o None. Es un JOB → no importa api/, usa core.postgres directo.
    Best-effort: si SQL falla, None → el motor sale 'sin_datos'."""
    from core.postgres import get_pool
    clause = f" WHERE {where}" if where else ""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT max(({ts_expr})::timestamptz) FROM {tabla}{clause}")
        row = cur.fetchone()
    return row[0] if row else None


# ── Secciones ────────────────────────────────────────────────────────────────

def _seccion_motores(en_rueda: bool) -> dict:
    out = {"items": [], "stale": 0, "muertos": 0}
    for label, cadencia, sql in MOTORES:
        item = {"motor": label, "coll": f"sql:{sql[0]}", "edad_s": None, "estado": "sin_datos"}
        try:
            ts = _frescura_sql(*sql)
            if ts is not None:
                edad = _edad_seg(ts)
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


def _jobruns_sql(sql: str, params=None) -> list[dict]:
    """Query a manager.job_runs (SQL-ONLY, decomiso Mongo 2026-06-28). Es un JOB →
    importa core.postgres directo (no api/)."""
    from psycopg.rows import dict_row

    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def _seccion_jobs() -> dict:
    """JOBS de la última hora + dailies vencidos desde manager.job_runs (SQL-ONLY,
    decomiso Mongo 2026-06-28: JobRunLogger ya escribe SQL-native, Mongo.JobRuns congelada)."""
    desde = _ahora() - timedelta(hours=1)
    ok = partial = error = 0
    fallas = []
    try:
        rows = _jobruns_sql(
            "SELECT tipo, status, data FROM manager.job_runs WHERE started_at >= %s",
            (desde,))
    except Exception as e:
        return {"ok": ok, "partial": partial, "error": error, "fallas": [],
                "vencidos": [], "err_seccion": f"{type(e).__name__}"}
    for d in rows:
        st = d.get("status")
        errs = (d.get("data") or {}).get("errors") or [""]
        if st == "ok":
            ok += 1
        elif st == "partial":
            partial += 1
            fallas.append({"tipo": d.get("tipo"), "status": "partial", "err": errs[0][:120]})
        else:
            error += 1
            fallas.append({"tipo": d.get("tipo"), "status": st, "err": errs[0][:120]})

    # Último run por tipo en UNA query (DISTINCT ON → 1 fila por tipo, la más reciente).
    vencidos = []
    try:
        ultimo_por_tipo = {
            r["tipo"]: r
            for r in _jobruns_sql(
                "SELECT DISTINCT ON (tipo) tipo, finished_at, started_at "
                "FROM manager.job_runs WHERE tipo = ANY(%s) "
                "ORDER BY tipo, started_at DESC",
                ([t for t, _ in DAILIES],))
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

    if mot["muertos"] or jb.get("error"):
        return "🔴", problemas
    if problemas:
        return "🟡", problemas
    return "🟢", problemas


# ── Construcción + render + persistencia ─────────────────────────────────────

def construir_informe() -> dict:
    now = _ahora()
    en_rueda = _en_rueda(now)

    rep: dict = {
        "ts": now,
        "en_rueda": en_rueda,
        "motores": _seccion_motores(en_rueda),
        "jobs": _seccion_jobs(),
    }
    veredicto, problemas = _consolidar(rep)
    rep["veredicto"] = veredicto
    rep["problemas"] = problemas
    return rep


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

    # Jobs
    jb = rep["jobs"]
    lines.append("")
    lines.append(f"*Jobs (1h):* {jb.get('ok', 0)} ok · {jb.get('partial', 0)} partial · {jb.get('error', 0)} error")
    for f in jb.get("fallas", [])[:5]:
        lines.append(f"  ✗ {f['tipo']} [{f['status']}]: {f['err']}")
    if jb.get("vencidos"):
        lines.append("  ⏰ vencidos: " + ", ".join(f"{v['tipo']}({v['horas']}h)" for v in jb["vencidos"]))

    return "\n".join(lines)


def main() -> int:
    dry = "--dry" in sys.argv
    no_tg = "--no-telegram" in sys.argv

    from core.job_runs import JobRunLogger
    with JobRunLogger("informe_salud") as jr:
        rep = construir_informe()
        msg = render_telegram(rep)
        jr.set_stat("veredicto", rep["veredicto"])
        jr.set_stat("n_problemas", len(rep["problemas"]))

        if dry:
            print(msg)
            print("\n[--dry: no se persiste ni se manda]")
            return 0

        # Persistir el snapshot estructurado en SQL `health_reports` (append-only).
        # retención 45d: cron prune_native externo (Postgres no tiene TTL).
        try:
            from core.pg_mirror import append_native
            append_native("health_reports", [{
                "ts": rep["ts"],
                "en_rueda": rep["en_rueda"],
                "veredicto": rep["veredicto"],
                "problemas": rep["problemas"],
                "data": {k: rep[k] for k in
                         ("motores", "jobs")},
            }])
        except Exception as e:
            jr.error(f"persist health_reports: {type(e).__name__}: {e}")

        if not no_tg:
            from core.notify import send_telegram
            send_telegram(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
