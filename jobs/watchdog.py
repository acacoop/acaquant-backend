"""jobs/watchdog.py — "el agente que evalúa solo": detecta jobs colgados y alerta.

Corre cada 5 min (cron). Escanea los procesos `python -m jobs.X` vivos en el
Droplet y, si alguno lleva corriendo más que su PRESUPUESTO, manda una alerta a
Telegram. Es la red de VISIBILIDAD que faltó el 2026-06-03 (descubrir_cuentas
corrió 5h sin que nadie se enterara). De MATAR el proceso se encarga el timeout
de deploy/run_job.sh; este watchdog solo AVISA (alertar es seguro; matar a ciegas
no).

NO mira los `engines.X` (motores): corren todo el día a propósito.
NO depende de JobRunLogger → cubre TODOS los jobs, incluso los que no lo usan.

Cooldown por job en Manager.WatchdogAlertas para no spamear cada 5 min.

Uso:
    python -m jobs.watchdog            # evalúa y alerta si corresponde
    python -m jobs.watchdog --dry-run  # imprime qué alertaría, sin mandar nada
"""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv

from core import atlas_api
from core.mongo import get_mongo_client
from core.notify import send_telegram

load_dotenv()  # ATLAS_* / ATLAS_RO_* del .env, para leer slow queries (best-effort)

# "Query targeting": una query es problema si EXAMINA mucho y DEVUELVE poco —
# COLLSCAN o IXSCAN poco selectivo da igual (el de boleto era COLLSCAN 488k:1; el
# de commodity es IXSCAN 174k:1). Alertamos si examina ≥ _EXAMINED_MIN docs Y la
# relación examinados/devueltos ≥ _RATIO. Esto es lo que Atlas NO te manda por mail.
_EXAMINED_MIN = 50_000
_RATIO_ALERTA = 100

# Presupuesto en SEGUNDOS por job = TIMEOUT de run_job.sh (deploy/crontab.txt) +
# margen. FILOSOFÍA (decisión 2026-06-04): el watchdog NO es un aviso temprano —
# solo alerta si el job sigue VIVO DESPUÉS del momento en que run_job.sh debería
# haberlo matado (SIGTERM al llegar al <timeout>, SIGKILL a +30s). O sea: una
# alerta significa "el mecanismo de kill FALLÓ" → anomalía real que requiere mano,
# NO una corrida lenta-pero-normal. Antes el presupuesto era < timeout → avisaba
# en cada corrida normal (falsa alarma; ver descubrir_cuentas, que se autolimita
# a 60 min y avisaba justo ahí). Para un job dentro de un chain, el timeout es el
# del CHAIN (lo comparten todos los del chain).
_GRACE_S = 5 * 60
_BUDGETS_S: dict[str, int] = {
    # ── standalone ──
    "descubrir_cuentas":       90 * 60 + _GRACE_S,   # run_job 90m (autocap propio 60m)
    "operaciones_informes":    25 * 60 + _GRACE_S,   # run_job 25m
    "consolidado_cuentas":     25 * 60 + _GRACE_S,   # run_job 25m
    "pnl_totales_precompute":  25 * 60 + _GRACE_S,   # run_job 25m
    "precios_acciones_daily":  30 * 60 + _GRACE_S,   # run_job 30m
    # ── negocio_chain (25m): negocio_movimientos → aranceles → fci_bilateral ──
    "negocio_movimientos":     25 * 60 + _GRACE_S,
    "aranceles":               25 * 60 + _GRACE_S,
    "fci_bilateral":           25 * 60 + _GRACE_S,
    # ── cashflow (30m, standalone) ──
    "cashflow":                30 * 60 + _GRACE_S,
    # ── flujo_contrapartes (30m, standalone) ──
    "flujo_contrapartes":      30 * 60 + _GRACE_S,
    # ── cierre_chain (25m): snapshot_cierre → fair_value ──
    "snapshot_cierre":         25 * 60 + _GRACE_S,
    "fair_value":              25 * 60 + _GRACE_S,
}
# Jobs no listados (todos de timeout ≤30m) → cubiertos con margen.
_DEFAULT_BUDGET_S = 30 * 60 + _GRACE_S
_COOLDOWN_S = 30 * 60   # no re-alertar el mismo job dentro de esta ventana

# El propio watchdog y los jobs sub-minuto no tiene sentido vigilarlos acá.
_IGNORAR = {"watchdog", "market_quotes", "comercial_warm"}

_RE_JOB = re.compile(r"-m\s+jobs\.(\w+)")


def _parse_ps(salida: str) -> list[tuple[str, int, int]]:
    """Parsea `ps -eo etimes=,pid=,args=` → [(nombre_job, pid, etimes_seg)].

    Pura (testeable). Solo devuelve procesos `python -m jobs.<nombre>`,
    excluyendo los de _IGNORAR.
    """
    out: list[tuple[str, int, int]] = []
    for linea in salida.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        partes = linea.split(None, 2)   # etimes, pid, args
        if len(partes) < 3:
            continue
        etimes_s, pid_s, args = partes
        m = _RE_JOB.search(args)
        if not m:
            continue
        nombre = m.group(1)
        if nombre in _IGNORAR:
            continue
        try:
            out.append((nombre, int(pid_s), int(etimes_s)))
        except ValueError:
            continue
    return out


def _jobs_corriendo() -> list[tuple[str, int, int]]:
    res = subprocess.run(
        ["ps", "-eo", "etimes=,pid=,args="],
        capture_output=True, text=True, timeout=15, check=False,
    )
    return _parse_ps(res.stdout)


def _check_db_queries(col, ahora: datetime, dry_run: bool) -> list[str]:
    """Alerta por Telegram cuando una query COLLSCAN escanea ≥ _SCAN_ALERTA docs,
    con su colección + appName — lo que Atlas NO te manda por mail (el 'CPU alto' sí).
    BEST-EFFORT: sin permiso de Performance Advisor (401) o sin ATLAS_* → se omite
    SIN romper el watchdog de procesos. Solo METADATOS (nunca valores) por diseño."""
    try:
        nodos = atlas_api.processes()
    except Exception:
        return []  # ATLAS_* sin configurar / API caída → no es crítico
    peores: dict[str, dict] = {}  # ns → la peor query COLLSCAN de esa colección
    for n in nodos:
        pid = n.get("id")
        if not pid:
            continue
        try:
            metas = atlas_api.slow_queries_meta(pid, n_logs=100)
        except Exception:
            continue  # 401 sin permiso de Performance Advisor → se omite
        for m in metas:
            dex = m.get("docsExamined") or 0
            ret = m.get("nreturned", m.get("nReturned")) or 0
            if dex < _EXAMINED_MIN or (dex / max(ret, 1)) < _RATIO_ALERTA:
                continue  # devuelve casi todo lo que examina → selectivo, no es el problema
            ns = m.get("ns", "?")
            if dex > (peores.get(ns, {}).get("docsExamined") or 0):
                peores[ns] = m
    if not peores:
        return []
    prev = col.find_one({"_id": "db_scan"})
    if prev and prev.get("last_alert_at") and \
            (ahora - prev["last_alert_at"]) < timedelta(seconds=_COOLDOWN_S):
        return []
    lineas = []
    for ns, m in sorted(peores.items(), key=lambda kv: -(kv[1].get("docsExamined") or 0))[:5]:
        dex = m.get("docsExamined") or 0
        ret = m.get("nreturned", m.get("nReturned")) or 0
        plan = m.get("planSummary", "?")
        app = f"  [{m['appName']}]" if m.get("appName") else ""
        lineas.append(f"• {ns} · {plan} · examinó {dex:,} para devolver {ret}{app}")
    msg = ("🔴 Watchdog DB: query(s) que examinan mucho y devuelven poco — lo que "
           "Atlas NO te avisa por mail:\n" + "\n".join(lineas) +
           "\n_falta un índice usable / la query no es selectiva (skill index-health)._")
    if not dry_run:
        send_telegram(msg)
        col.update_one({"_id": "db_scan"}, {"$set": {"last_alert_at": ahora}}, upsert=True)
    return lineas


def _check_motores(col, ahora: datetime, dry_run: bool) -> list[str]:
    """Alerta por Telegram cuando un MOTOR de mercado está MUERTO (sin datos frescos
    > 15 min) en horario de rueda. El watchdog corre cada 5 min → la caída se detecta
    rápido, a diferencia de `informe_salud` (horario). Cooldown POR motor (30 min).

    Reusa `informe_salud._seccion_motores` (frescura de la colección de cada motor):
    un motor en loop de restart NO escribe → su colección se queda stale → 'muerto'.
    Es la red que faltó cuando motor_options entró en loop sin avisar (2026-06-16)."""
    from jobs.informe_salud import _en_rueda, _seccion_motores

    if not _en_rueda(ahora.replace(tzinfo=None)):
        return []  # motores apagados fuera de rueda → no alertar
    try:
        sec = _seccion_motores(get_mongo_client(), en_rueda=True)
    except Exception:
        return []  # no romper el watchdog de jobs si la lectura de motores falla

    alertados: list[str] = []
    for it in sec["items"]:
        if it["estado"] != "muerto":
            continue
        key = f"motor:{it['motor']}"
        prev = col.find_one({"_id": key})
        if prev and prev.get("last_alert_at") and \
                (ahora - prev["last_alert_at"]) < timedelta(seconds=_COOLDOWN_S):
            continue
        edad = it.get("edad_s")
        edad_txt = f"{edad // 60} min" if isinstance(edad, int) else "sin datos"
        msg = (f"🔴 Motor CAÍDO: *{it['motor']}* sin datos frescos hace *{edad_txt}* "
               f"(en rueda).\nLa colección `{it['coll']}` no se actualiza → el motor "
               f"está muerto o en loop de restart.\n_triage: `/motor-status` o "
               f"`journalctl -u motor_<x>.service -n 50`._")
        if not dry_run:
            send_telegram(msg)
            col.update_one({"_id": key},
                           {"$set": {"last_alert_at": ahora, "edad_s": edad}}, upsert=True)
        alertados.append(it["motor"])
    return alertados


def run(dry_run: bool = False) -> dict:
    ahora = datetime.now(UTC)
    col = get_mongo_client()["Manager"]["WatchdogAlertas"]
    corriendo = _jobs_corriendo()

    alertados, revisados = [], []
    for nombre, pid, etimes in corriendo:
        budget = _BUDGETS_S.get(nombre, _DEFAULT_BUDGET_S)
        revisados.append((nombre, etimes, budget))
        if etimes <= budget:
            continue
        # Cooldown: ¿ya alertamos este job hace poco?
        prev = col.find_one({"_id": nombre})  # perf-ok: PERF002 — N = jobs vivos (~10), lookup por _id
        if prev and prev.get("last_alert_at") and \
                (ahora - prev["last_alert_at"]) < timedelta(seconds=_COOLDOWN_S):
            continue
        mins = etimes // 60
        bmins = budget // 60
        msg = (f"🔴 Watchdog: `{nombre}` SIGUE VIVO hace *{mins} min* (PID {pid}).\n"
               f"Ya superó su timeout de run_job.sh + margen ({bmins} min) → "
               f"el kill automático NO funcionó (anomalía real).\n"
               f"Matar a mano: `kill {pid}`.  _revisar RUNBOOK / por qué no murió_")
        if not dry_run:
            send_telegram(msg)
            col.update_one({"_id": nombre},
                           {"$set": {"last_alert_at": ahora, "etimes": etimes, "pid": pid}},
                           upsert=True)
        alertados.append((nombre, mins, bmins))

    # Queries COLLSCAN escaneando de más (best-effort; no rompe si ATLAS_* falta).
    db_scans = _check_db_queries(col, ahora, dry_run)

    # Motores de mercado caídos/en loop (frescura de datos en rueda).
    motores_caidos = _check_motores(col, ahora, dry_run)

    if dry_run:
        print(f"[DRY] jobs corriendo: {revisados}")
        print(f"[DRY] alertaría jobs: {alertados}")
        print(f"[DRY] alertaría DB COLLSCAN: {db_scans}")
        print(f"[DRY] alertaría motores caídos: {motores_caidos}")
    return {"corriendo": len(corriendo), "alertados": len(alertados),
            "jobs": [a[0] for a in alertados], "db_scans": len(db_scans),
            "motores_caidos": motores_caidos}


def main() -> int:
    res = run(dry_run="--dry-run" in sys.argv)
    print(f"→ {res}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
