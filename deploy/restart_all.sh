#!/usr/bin/env bash
# deploy/restart_all.sh — reinicia la API + TODOS los motores que estén corriendo.
#
# ⚠️ **NO es el camino del deploy normal** (2026-08-18). `deploy/deploy.sh` YA NO
# lo llama: reiniciar los motores en rueda corta el feed de precios de la mesa, y
# el 95% de los deploys tocan la API y no los motores. Esto queda para el caso
# explícito —cambió código de `engines/`/`core/`/`quant/` y hay que bajarlo YA—
# y se corre a mano o con `deploy.sh --con-motores`, preferentemente fuera de
# rueda (no 13-20 UTC L-V).
#
# Uso (en el Droplet, después de `git pull`):
#     bash deploy/restart_all.sh
#
# Qué hace:
#   - `systemctl restart api.service` (siempre — la API corre 24/7).
#   - `systemctl try-restart` de cada motor_*.service de deploy/systemd/:
#     try-restart SOLO reinicia los que están ACTIVOS. Los motores los
#     prende/apaga cron (L-V 13-20 UTC) — fuera de rueda no hay que
#     levantarlos, y con esto el script es seguro de correr a cualquier hora.
#   - Al final lista las units en estado failed (si hay alguna, ahí está
#     el problema del deploy).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

echo "→ restart api.service"
systemctl restart api.service

for unit in deploy/systemd/motor_*.service; do
    u="$(basename "$unit")"
    if systemctl is-active --quiet "$u"; then
        echo "→ restart $u (activo)"
        systemctl try-restart "$u"
    else
        echo "· $u inactivo (lo maneja cron) — no se toca"
    fi
done

echo
echo "== Units en failed (vacío = todo OK) =="
systemctl --failed --no-legend --plain | grep -E "api|motor" || echo "(ninguna)"
