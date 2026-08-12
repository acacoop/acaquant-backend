#!/usr/bin/env bash
# deploy/install_tenencia_live.sh — instala el daemon tenencia_live en el Droplet.
#
# Uso (después de `git pull`):
#     bash deploy/install_tenencia_live.sh              # instala el unit + muestra el diff del cron
#     bash deploy/install_tenencia_live.sh --con-cron   # además APLICA el crontab (con backup)
#     bash deploy/install_tenencia_live.sh --smoke      # además corre la prueba de 3 cuentas
#
# Es IDEMPOTENTE: correrlo dos veces no rompe nada.
#
# QUÉ HACE Y QUÉ NO
#   ✓ copia deploy/systemd/tenencia_live.service a /etc/systemd/system/ y recarga systemd
#   ✓ compara el crontab VIVO contra deploy/crontab.txt y muestra la diferencia
#   ✓ con --con-cron, aplica el crontab guardando antes un backup con fecha
#   ✗ NO hace `systemctl enable` — el daemon lo prende y apaga CRON (igual que los
#     motores). Con enable arrancaría también en cada boot, fuera de la ventana.
#   ✗ NO hace falta correr apply_schema: el job crea su tabla solo al arrancar
#     (`_ensure_schema`), y no toca ninguna tabla existente.
#
# ⚠️ SOBRE EL CRONTAB: `crontab archivo` REEMPLAZA el crontab vivo entero. Si
# alguien editó el cron a mano en el servidor y eso nunca volvió al repo, aplicarlo
# lo borra. Por eso el default es SOLO mostrar el diff: mirás qué cambia y recién
# después decidís. Si el diff trae líneas que no reconocés, NO lo apliques —
# primero llevá esos cambios a deploy/crontab.txt.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

UNIT="tenencia_live.service"
SRC="deploy/systemd/$UNIT"
DST="/etc/systemd/system/$UNIT"
CRON_SRC="$(pwd)/deploy/crontab.txt"

CON_CRON=0
SMOKE=0
for a in "$@"; do
    case "$a" in
        --con-cron) CON_CRON=1 ;;
        --smoke)    SMOKE=1 ;;
        *) echo "opción desconocida: $a"; exit 2 ;;
    esac
done

# ── 1) unit de systemd ────────────────────────────────────────────────────────
echo "── 1) systemd"
if [[ ! -f "$SRC" ]]; then
    echo "   ✗ no existe $SRC — ¿hiciste git pull?"
    exit 1
fi
if [[ -f "$DST" ]] && cmp -s "$SRC" "$DST"; then
    echo "   · $UNIT ya está instalado y es idéntico — no lo toco"
else
    cp "$SRC" "$DST" && echo "   ✓ copiado $SRC → $DST"
    systemctl daemon-reload && echo "   ✓ systemctl daemon-reload"
fi
echo "   estado actual: $(systemctl is-active "$UNIT" 2>/dev/null || echo inactive)"
echo "   (NO se hace 'enable' a propósito: lo prende y apaga cron, como los motores)"

# ── 2) crontab ────────────────────────────────────────────────────────────────
echo
echo "── 2) crontab"
TMP_VIVO="$(mktemp)"
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
        echo "   ⚠ NO se aplicó. Revisá el diff de arriba."
        echo "     Si las líneas con '<' son cambios hechos a mano en el servidor que"
        echo "     NO están en el repo, llevalos primero a deploy/crontab.txt."
        echo "     Si está todo bien, volvé a correr con --con-cron"
    fi
fi
rm -f "$TMP_VIVO"

# ── 3) smoke (opcional) ───────────────────────────────────────────────────────
if [[ "$SMOKE" -eq 1 ]]; then
    echo
    echo "── 3) smoke: 3 cuentas, un solo barrido, sin esperar al backfill"
    ./venv/bin/python -m jobs.tenencia_live \
        --cuentas 805,1346,1839 --una-pasada --sin-esperar-backfill
fi

echo
echo "== Listo =="
echo "Para verlo correr a mano:  systemctl start $UNIT && journalctl -u $UNIT -f"
echo "Para pararlo:              systemctl stop $UNIT"
