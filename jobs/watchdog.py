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

from core.mongo import get_mongo_client
from core.notify import send_telegram

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
    # ── aum_chain (25m): aum → sync_api_copies ──
    "aum":                     25 * 60 + _GRACE_S,
    # ── cashflow_chain (30m): cashflow → sync_api_copies ──
    "cashflow":                30 * 60 + _GRACE_S,
    # ── flujo_chain (30m): flujo_contrapartes → sync_api_copies ──
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
        prev = col.find_one({"_id": nombre})
        if prev and prev.get("last_alert_at") and \
                (ahora - prev["last_alert_at"]) < timedelta(seconds=_COOLDOWN_S):
            continue
        mins = etimes // 60
        bmins = budget // 60
        msg = (f"🔴 Watchdog: `{nombre}` SIGUE VIVO hace *{mins} min* (PID {pid}).\n"
               f"Ya superó su timeout de run_job.sh + margen ({bmins} min) → "
               f"el kill automático NO funcionó (anomalía real).\n"
               f"Matar a mano: `kill {pid}`.  _revisar AUDITORIA_DATOS / por qué no murió_")
        if not dry_run:
            send_telegram(msg)
            col.update_one({"_id": nombre},
                           {"$set": {"last_alert_at": ahora, "etimes": etimes, "pid": pid}},
                           upsert=True)
        alertados.append((nombre, mins, bmins))

    if dry_run:
        print(f"[DRY] jobs corriendo: {revisados}")
        print(f"[DRY] alertaría: {alertados}")
    return {"corriendo": len(corriendo), "alertados": len(alertados),
            "jobs": [a[0] for a in alertados]}


def main() -> int:
    res = run(dry_run="--dry-run" in sys.argv)
    print(f"→ {res}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
