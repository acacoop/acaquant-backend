#!/usr/bin/env bash
# deploy/run_job.sh — wrapper de jobs de cron con LOCK + TIMEOUT + LOG.
#
# Reemplaza el patrón crudo del crontab (`cd ... && python -m jobs.X >> log`).
# Resuelve las dos causas del incidente de CPU del 2026-06-03:
#   1) LOCK (flock -n): si la corrida anterior del MISMO job sigue viva, esta se
#      SALTEA en vez de apilarse. (fci_bilateral se relanzaba cada hora encima
#      de sí mismo → carga multiplicada.)
#   2) TIMEOUT: si el job excede su presupuesto, lo mata (SIGTERM, luego SIGKILL
#      a los 30s). (Un job de descubrimiento corrió 5h; fci_bilateral colgado.)
# Además loguea START/OK/SKIP/TIMEOUT/ERROR con timestamp UTC a logs/<nombre>.log.
#
# Uso (en crontab):
#   <cron> /root/TradingAV/deploy/run_job.sh <nombre> <timeout> '<comando shell>'
# Ej:
#   ... run_job.sh bcra 10m 'cd /root/TradingAV && venv/bin/python -m jobs.bcra --today'
#   ... run_job.sh negocio_chain 25m 'cd /root/TradingAV && venv/bin/python -m jobs.negocio_movimientos && venv/bin/python -m jobs.aranceles'
#
# El comando se pasa entre comillas SIMPLES (un solo argumento); el redirect al
# log lo hace este wrapper, NO el crontab. <timeout> usa formato de timeout(1):
# 50s, 25m, 2h.
set -uo pipefail

NAME="${1:?uso: run_job.sh <nombre> <timeout> '<cmd>'}"
TIMEOUT="${2:?falta <timeout> (ej: 25m)}"
shift 2
CMD="$*"

ROOT="/root/TradingAV"
LOG="${ROOT}/logs/${NAME}.log"
LOCK_DIR="/run/tradingav"
LOCK="${LOCK_DIR}/${NAME}.lock"
mkdir -p "$LOCK_DIR" "${ROOT}/logs"

ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

# fd 200 → lockfile. -n = no bloquear: si ya está tomado, salimos (skip).
exec 200>"$LOCK"
if ! flock -n 200; then
  echo "[$(ts)] SKIP ${NAME}: instancia anterior sigue corriendo (${LOCK})" >> "$LOG"
  exit 0
fi

echo "[$(ts)] START ${NAME} (timeout=${TIMEOUT})" >> "$LOG"
t0=$(date +%s)
timeout --signal=TERM --kill-after=30s "$TIMEOUT" bash -c "$CMD" >> "$LOG" 2>&1
rc=$?
dt=$(( $(date +%s) - t0 ))
case "$rc" in
  0)   echo "[$(ts)] OK ${NAME}: ${dt}s" >> "$LOG" ;;
  124) echo "[$(ts)] TIMEOUT ${NAME}: matado tras ${TIMEOUT} (corrió ${dt}s)" >> "$LOG" ;;
  *)   echo "[$(ts)] ERROR ${NAME}: exit=${rc} (${dt}s)" >> "$LOG" ;;
esac
exit "$rc"
