#!/usr/bin/env bash
# deploy/restart_all.sh — reinicia la API y, si corresponde, los motores.
#
# Uso (en el Droplet, después de `git pull`):
#     bash deploy/restart_all.sh              # API + motores ACTIVOS
#     bash deploy/restart_all.sh --solo-api   # SOLO la API (no toca los motores)
#
# Qué hace:
#   - `systemctl restart api.service` (siempre — la API corre 24/7 y es barata
#     de reiniciar: ~1s de parada + ~4s de arranque, medido 2026-08-13).
#   - `systemctl try-restart` de cada motor_*.service de deploy/systemd/:
#     try-restart SOLO reinicia los que están ACTIVOS. Los motores los
#     prende/apaga cron (L-V 13-20 UTC) — fuera de rueda no hay que
#     levantarlos, y con esto el script es seguro de correr a cualquier hora.
#   - Al final lista las units en estado failed (si hay alguna, ahí está
#     el problema del deploy).
#
# ⚠️ POR QUÉ EXISTE `--solo-api` (2026-08-13). Reiniciar un motor NO es gratis:
# tira su sesión websocket contra ROFEX y tiene que reconectar y re-suscribir.
# En rueda eso es un hueco real de datos de mercado. Los motores solo importan
# `core/` y `quant/`, así que un cambio en `api/`, `docs/`, `tests/` o `scripts/`
# NO los afecta y reiniciarlos es puro costo sin beneficio. `deploy.sh` decide
# solo mirando qué archivos cambió el pull; acá queda el interruptor manual.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

SOLO_API=0
for arg in "$@"; do
    case "$arg" in
        --solo-api) SOLO_API=1 ;;
        *) echo "opción desconocida: $arg (usá --solo-api)"; exit 2 ;;
    esac
done

echo "→ restart api.service"
systemctl restart api.service

if [ "$SOLO_API" = "1" ]; then
    echo "· motores NO se tocan (--solo-api): su código no cambió"
else
    for unit in deploy/systemd/motor_*.service; do
        u="$(basename "$unit")"
        if systemctl is-active --quiet "$u"; then
            echo "→ restart $u (activo)"
            systemctl try-restart "$u"
        else
            echo "· $u inactivo (lo maneja cron) — no se toca"
        fi
    done
fi

echo
echo "== Units en failed (vacío = todo OK) =="
systemctl --failed --no-legend --plain | grep -E "api|motor" || echo "(ninguna)"
