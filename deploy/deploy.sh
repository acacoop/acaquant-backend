#!/usr/bin/env bash
# deploy/deploy.sh — deploy COMPLETO del backend en el Droplet, en un comando.
#
# Uso (en el Droplet):
#     cd /root/TradingAV && git pull && bash deploy/deploy.sh
#
# El `git pull` de adelante es solo para traer ESTE script actualizado; el
# script vuelve a pullear igual (es idempotente) y deja registrado en qué commit
# quedó parado el server.
#
# Qué hace, en orden:
#   1. git pull  — trae el código nuevo y muestra el commit resultante.
#   2. apply_schema — crea las tablas/columnas/índices que falten. Es idempotente
#      y NO destructivo (solo CREATE ... IF NOT EXISTS y semillas guardadas: no
#      hay un solo DROP/DELETE/TRUNCATE). Se puede saltear con --sin-schema.
#   3. restart_all.sh — reinicia api.service y hace try-restart de los motores
#      que estén ACTIVOS (los apagados los maneja cron; no se prenden a destiempo).
#   4. Smoke — pega a /api/health local y muestra el estado del service.
#
# Corta al primer fallo y dice exactamente qué paso murió. Si algo se rompe NO
# hace rollback solo: el rollback es una decisión, no un efecto secundario.
#
# ⚠️ REGLA #4 — apply_schema puede crear índices. Sobre tablas nuevas o chicas es
# instantáneo, pero si el cambio agrega un índice sobre una tabla grande conviene
# correr esto FUERA DE RUEDA (no 13-20 UTC L-V). Ante la duda: --sin-schema ahora
# y el schema después.
set -uo pipefail

cd "$(dirname "$0")/.." || { echo "no pude entrar al repo"; exit 1; }
REPO="$(pwd)"
PY="$REPO/venv/bin/python"
[ -x "$PY" ] || PY="python3"

CON_SCHEMA=1
for arg in "$@"; do
    case "$arg" in
        --sin-schema) CON_SCHEMA=0 ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *) echo "opción desconocida: $arg (usá --sin-schema o --help)"; exit 2 ;;
    esac
done

morir() { echo; echo "❌ FALLÓ: $1"; echo "   El deploy se detuvo acá. Nada de lo que sigue corrió."; exit 1; }

echo "════════════════════════════════════════════════════════════"
echo " DEPLOY BACKEND — $REPO"
echo " $(date '+%Y-%m-%d %H:%M:%S %Z')   python: $PY"
echo "════════════════════════════════════════════════════════════"

# ── 1. Código ───────────────────────────────────────────────────────────────
echo
echo "▶ 1/4  git pull"
ANTES="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
git pull --ff-only || morir "git pull (¿hay cambios locales sin commitear en el server?)"
DESPUES="$(git rev-parse --short HEAD)"
if [ "$ANTES" = "$DESPUES" ]; then
    echo "   ya estaba en $DESPUES (no había nada nuevo)"
else
    echo "   $ANTES → $DESPUES"
    git log --oneline "$ANTES..$DESPUES" | sed 's/^/     /'
fi

# ── 2. Schema ───────────────────────────────────────────────────────────────
echo
if [ "$CON_SCHEMA" = "1" ]; then
    echo "▶ 2/4  apply_schema (idempotente, no destructivo)"
    "$PY" -m scripts.apply_schema || morir "apply_schema — la API NO se reinició, sigue corriendo la versión anterior"
else
    echo "▶ 2/4  apply_schema SALTEADO (--sin-schema)"
fi

# ── 3. Servicios ────────────────────────────────────────────────────────────
echo
echo "▶ 3/4  restart api.service + motores activos"
bash deploy/restart_all.sh || morir "restart_all.sh — mirá 'journalctl -u api.service -n 50'"

# ── 4. Smoke ────────────────────────────────────────────────────────────────
echo
echo "▶ 4/4  smoke"
sleep 3
HEALTH="$(curl -s --max-time 10 http://localhost:8000/api/health || true)"
if [ -n "$HEALTH" ]; then
    echo "   /api/health → $HEALTH"
else
    echo "   ⚠️  /api/health no respondió. Estado del service:"
    systemctl status api.service --no-pager -n 15 || true
    morir "la API no levantó — 'journalctl -u api.service -n 50' tiene el motivo"
fi

echo
echo "════════════════════════════════════════════════════════════"
echo " ✅ DEPLOY OK — backend en $DESPUES"
echo "════════════════════════════════════════════════════════════"
