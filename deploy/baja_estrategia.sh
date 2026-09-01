#!/usr/bin/env bash
# deploy/baja_estrategia.sh — completa la baja de ESTRATEGIA QUANT en el Droplet.
#
# Uso (después de `git pull` + `bash deploy/deploy.sh`):
#     bash deploy/baja_estrategia.sh              # apaga y desinstala + muestra el diff del cron
#     bash deploy/baja_estrategia.sh --con-cron   # además APLICA el crontab (con backup)
#
# Es IDEMPOTENTE: correrlo dos veces no rompe nada y no falla si ya está todo hecho.
#
# POR QUÉ HACE FALTA UN PASO APARTE
# El commit 98e80fb borró del REPO el motor, el resolver, la unit y las líneas de
# cron. Pero `git pull` no puede apagar un proceso ni editar el crontab vivo:
#   · la unit se COPIA a /etc/systemd/system/ (no es un symlink al repo), así que
#     sigue instalada y corriendo con el código viejo cargado en memoria;
#   · el mismo deploy corrió apply_schema, que ejecutó `DROP SCHEMA estrategia
#     CASCADE` → el motor que quedó vivo está escribiendo contra tablas que ya no
#     existen, y el resolver arranca cada 5 minutos para lo mismo.
# O sea: hasta correr esto, el servidor tiene DOS procesos fallando en silencio.
#
# QUÉ HACE Y QUÉ NO
#   ✓ stop + disable de motor_estrategia.service, borra la unit y recarga systemd
#   ✓ reset-failed (si ya quedó en estado failed, para que no arrastre el error)
#   ✓ compara el crontab VIVO contra deploy/crontab.txt y muestra la diferencia
#   ✓ con --con-cron, aplica el crontab guardando antes un backup con fecha
#   ✗ NO toca la base: el DROP SCHEMA ya lo hizo apply_schema en el deploy
#   ✗ NO toca ningún otro motor
#
# ⚠️ SOBRE EL CRONTAB: `crontab archivo` REEMPLAZA el crontab vivo entero. Si
# alguien editó el cron a mano en el servidor y eso nunca volvió al repo, aplicarlo
# lo borra. Por eso el default es SOLO mostrar el diff: mirás qué cambia y recién
# después decidís. Si el diff trae líneas que no reconocés, NO lo apliques —
# primero llevá esos cambios a deploy/crontab.txt.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

UNIT="motor_estrategia.service"
DST="/etc/systemd/system/$UNIT"
CRON_SRC="$(pwd)/deploy/crontab.txt"

CON_CRON=0
for a in "$@"; do
    case "$a" in
        --con-cron) CON_CRON=1 ;;
        *) echo "opción desconocida: $a"; exit 2 ;;
    esac
done

echo "════════════════════════════════════════════════════════════"
echo " BAJA DE ESTRATEGIA QUANT — $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "════════════════════════════════════════════════════════════"

# ── 1) el motor ───────────────────────────────────────────────────────────────
echo
echo "── 1) systemd: $UNIT"
ESTADO="$(systemctl is-active "$UNIT" 2>/dev/null || true)"
echo "   estado antes: ${ESTADO:-desconocido}"

if systemctl list-unit-files "$UNIT" >/dev/null 2>&1 && \
   systemctl cat "$UNIT" >/dev/null 2>&1; then
    systemctl stop "$UNIT" 2>/dev/null && echo "   ✓ stop" || echo "   · no estaba corriendo"
    systemctl disable "$UNIT" 2>/dev/null >/dev/null && echo "   ✓ disable" || echo "   · no estaba enabled"
    systemctl reset-failed "$UNIT" 2>/dev/null && echo "   ✓ reset-failed" || true
else
    echo "   · systemd no conoce la unit — nada que apagar"
fi

if [[ -f "$DST" ]]; then
    rm -f "$DST" && echo "   ✓ borrada $DST"
    systemctl daemon-reload && echo "   ✓ systemctl daemon-reload"
else
    echo "   · $DST ya no existe"
fi

echo "   estado final: $(systemctl is-active "$UNIT" 2>/dev/null || echo 'inactive/not-found')"

# ── 2) crontab ────────────────────────────────────────────────────────────────
echo
echo "── 2) crontab (se van 4 líneas: restart/stop del motor + 2 del resolver)"
TMP_VIVO="$(mktemp)"
trap 'rm -f "$TMP_VIVO"' EXIT
crontab -l > "$TMP_VIVO" 2>/dev/null || : > "$TMP_VIVO"

if diff -q "$TMP_VIVO" "$CRON_SRC" >/dev/null 2>&1; then
    echo "   ✓ el crontab vivo YA es igual a deploy/crontab.txt — nada que hacer"
else
    echo "   Diferencias (< vivo en el servidor · > deploy/crontab.txt del repo):"
    diff "$TMP_VIVO" "$CRON_SRC" | sed 's/^/     /'
    echo
    if [[ "$CON_CRON" -eq 1 ]]; then
        BACKUP="/root/crontab.backup.$(date +%Y%m%d-%H%M%S)"
        cp "$TMP_VIVO" "$BACKUP"
        echo "   backup del crontab vivo → $BACKUP"
        if crontab "$CRON_SRC"; then
            echo "   ✓ crontab aplicado desde deploy/crontab.txt"
        else
            echo "   ✗ falló al aplicar — el crontab vivo quedó como estaba"
        fi
    else
        echo "   ⚠ NO se aplicó. Revisá el diff de arriba y, si está bien:"
        echo "     bash deploy/baja_estrategia.sh --con-cron"
    fi
fi

# ── 3) sobra algo? ────────────────────────────────────────────────────────────
echo
echo "── 3) control final"
RESTOS="$(crontab -l 2>/dev/null | grep -c 'estrategia' || true)"
echo "   líneas con 'estrategia' en el crontab vivo: $RESTOS  (tiene que ser 0)"
echo "   unit instalada: $([[ -f "$DST" ]] && echo 'SÍ ⚠' || echo 'no ✓')"
echo
echo "════════════════════════════════════════════════════════════"
