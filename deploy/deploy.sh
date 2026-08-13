#!/usr/bin/env bash
# deploy/deploy.sh — deploy COMPLETO del backend en el Droplet, en un comando.
#
# Uso (en el Droplet):
#     cd /root/TradingAV && bash deploy/deploy.sh
#
# ⚠️ SIN `git pull` adelante. El script PULLEA ÉL MISMO, y necesita hacerlo para
# saber QUÉ CAMBIÓ: con un pull previo el rango queda vacío y no puede decidir si
# los motores necesitan restart (ver abajo). Si igual pulleás antes, el deploy
# funciona pero reinicia los motores por las dudas.
#
# Qué hace, en orden:
#   1. git pull  — trae el código y calcula la lista de archivos que cambiaron.
#   2. apply_schema — crea las tablas/columnas/índices que falten. Idempotente y
#      NO destructivo (solo CREATE ... IF NOT EXISTS y semillas guardadas: no hay
#      un solo DROP/DELETE/TRUNCATE). Se saltea con --sin-schema.
#   3. restart — SIEMPRE la API; los MOTORES solo si su código cambió (ver abajo).
#   4. Smoke — pega a /api/health local.
#   5. Avisa si algún unit file instalado difiere del repo.
#
# ── QUÉ SE REINICIA Y POR QUÉ ───────────────────────────────────────────────
# Reiniciar un motor NO es gratis: tira su sesión websocket contra ROFEX y tiene
# que reconectar y re-suscribir. En rueda (13-20 UTC L-V) eso es un hueco real de
# datos de mercado. Y los motores solo importan `core/` y `quant/`: un cambio en
# `api/`, `docs/`, `tests/` o `scripts/` no los toca ni un poco.
# Por eso el default es DECIDIR: si el pull tocó core/quant/engines/config.py/
# requirements.txt → se reinician; si no, se saltean y el script lo dice.
# `jobs/` tampoco cuenta: los crons levantan código nuevo en su próxima corrida.
# Forzar en cualquier dirección: --con-motores / --solo-api.
#
# Corta al primer fallo y dice exactamente qué paso murió. Si algo se rompe NO
# hace rollback solo: el rollback es una decisión, no un efecto secundario.
#
# ⚠️ REGLA #4 — apply_schema puede crear índices. Sobre tablas nuevas o chicas es
# instantáneo, pero si el cambio agrega un índice sobre una tabla grande conviene
# correr esto FUERA DE RUEDA. Ante la duda: --sin-schema ahora y el schema después.
set -uo pipefail

# TODO el cuerpo va adentro de main() y se invoca al final, a propósito: bash lee
# el script POR PARTES mientras lo ejecuta, así que si el paso 1 se actualiza a sí
# mismo (este archivo cambió en el pull) el intérprete podía seguir leyendo desde
# un offset que ahora apunta a otra línea. Con todo adentro de una función, bash
# parsea el archivo COMPLETO antes de ejecutar nada y el auto-update es inocuo.
main() {

cd "$(dirname "$0")/.." || { echo "no pude entrar al repo"; exit 1; }
REPO="$(pwd)"
PY="$REPO/venv/bin/python"
[ -x "$PY" ] || PY="python3"

CON_SCHEMA=1
FORZAR=""            # "" = decidir solo | "si" = --con-motores | "no" = --solo-api
for arg in "$@"; do
    case "$arg" in
        --sin-schema)  CON_SCHEMA=0 ;;
        --con-motores) FORZAR="si" ;;
        --solo-api)    FORZAR="no" ;;
        -h|--help)     sed -n '2,40p' "$0"; exit 0 ;;
        *) echo "opción desconocida: $arg (--sin-schema | --con-motores | --solo-api)"; exit 2 ;;
    esac
done

morir() { echo; echo "❌ FALLÓ: $1"; echo "   El deploy se detuvo acá. Nada de lo que sigue corrió."; exit 1; }

echo "════════════════════════════════════════════════════════════"
echo " DEPLOY BACKEND — $REPO"
echo " $(date '+%Y-%m-%d %H:%M:%S %Z')   python: $PY"
echo "════════════════════════════════════════════════════════════"

# ── 1. Código ───────────────────────────────────────────────────────────────
echo
echo "▶ 1/5  git pull"
ANTES="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
git pull --ff-only || morir "git pull (¿hay cambios locales sin commitear en el server?)"
DESPUES="$(git rev-parse --short HEAD)"
CAMBIOS=""
if [ "$ANTES" = "$DESPUES" ]; then
    echo "   ya estaba en $DESPUES (no había nada nuevo que traer)"
else
    echo "   $ANTES → $DESPUES"
    git log --oneline "$ANTES..$DESPUES" | sed 's/^/     /'
    CAMBIOS="$(git diff --name-only "$ANTES..$DESPUES" 2>/dev/null || true)"
fi

# ── 2. ¿Los motores necesitan restart? ──────────────────────────────────────
# Prefijos cuyo código SÍ corre adentro de un motor. `jobs/` no está: los crons
# levantan código nuevo en su próxima corrida sin que nadie reinicie nada.
MOTORES_PATRON='^(core/|quant/|engines/|config\.py$|requirements\.txt$)'
if [ "$FORZAR" = "si" ]; then
    MOTORES=1; MOTIVO="forzado con --con-motores"
elif [ "$FORZAR" = "no" ]; then
    MOTORES=0; MOTIVO="forzado con --solo-api"
elif [ -z "$CAMBIOS" ]; then
    # Sin rango no se puede saber qué cambió (típico: se pulleó por afuera).
    # Fail-safe: reiniciar. Es preferible un hueco de datos a dejar un motor
    # corriendo código viejo sin que nadie se entere.
    MOTORES=1; MOTIVO="no pude ver qué cambió (¿pulleaste antes de correr esto?) → por las dudas"
elif echo "$CAMBIOS" | grep -qE "$MOTORES_PATRON"; then
    MOTORES=1
    MOTIVO="cambió código que corre en los motores: $(echo "$CAMBIOS" | grep -E "$MOTORES_PATRON" | head -4 | tr '\n' ' ')"
else
    MOTORES=0
    MOTIVO="el cambio no toca core/quant/engines → los motores corren el mismo código"
fi

# ── 3. Schema ───────────────────────────────────────────────────────────────
echo
if [ "$CON_SCHEMA" = "1" ]; then
    echo "▶ 2/5  apply_schema (idempotente, no destructivo)"
    "$PY" -m scripts.apply_schema || morir "apply_schema — la API NO se reinició, sigue corriendo la versión anterior"
else
    echo "▶ 2/5  apply_schema SALTEADO (--sin-schema)"
fi

# ── 4. Servicios ────────────────────────────────────────────────────────────
echo
if [ "$MOTORES" = "1" ]; then
    echo "▶ 3/5  restart api.service + motores activos"
    echo "   motivo: $MOTIVO"
    bash deploy/restart_all.sh || morir "restart_all.sh — mirá 'journalctl -u api.service -n 50'"
else
    echo "▶ 3/5  restart SOLO api.service"
    echo "   motivo: $MOTIVO"
    echo "   (reiniciar un motor le tira la sesión de ROFEX; en rueda eso es un hueco de datos)"
    bash deploy/restart_all.sh --solo-api || morir "restart_all.sh — mirá 'journalctl -u api.service -n 50'"
fi

# ── 5. Smoke ────────────────────────────────────────────────────────────────
echo
echo "▶ 4/5  smoke"
sleep 3
HEALTH="$(curl -s --max-time 10 http://localhost:8000/api/health || true)"
if [ -n "$HEALTH" ]; then
    echo "   /api/health → $HEALTH"
else
    echo "   ⚠️  /api/health no respondió. Estado del service:"
    systemctl status api.service --no-pager -n 15 || true
    morir "la API no levantó — 'journalctl -u api.service -n 50' tiene el motivo"
fi

# ── 6. Deriva del unit file ─────────────────────────────────────────────────
# `git pull` trae deploy/systemd/*.service al repo, pero NO los instala: systemd
# lee /etc/systemd/system. Si alguien editó el unit a mano en el Droplet, o si un
# cambio del repo nunca se instaló, el archivo versionado dice una cosa y el
# servicio hace otra — y eso se descubre justo cuando estás debuggeando algo.
# Detectado el 2026-08-13: el repo declara RuntimeMaxSec=8h y systemd reportaba
# RuntimeMaxUSec=infinity. Solo AVISA: instalar un unit es una decisión, no un
# efecto secundario de deployar código.
echo
echo "▶ 5/5  unit files: repo vs instalado"
DERIVA=0
for f in deploy/systemd/*.service; do
    u="$(basename "$f")"
    inst="$(systemctl show "$u" -p FragmentPath --value 2>/dev/null || true)"
    if [ -z "$inst" ] || [ ! -f "$inst" ]; then
        echo "   ·  $u — no instalado (no lo maneja systemd acá)"
    elif ! diff -q "$f" "$inst" >/dev/null 2>&1; then
        echo "   ⚠️  $u DIFIERE del repo → $inst"
        DERIVA=1
    fi
done
if [ "$DERIVA" = "1" ]; then
    echo "   Para alinearlos:  cp deploy/systemd/<unit> /etc/systemd/system/ &&"
    echo "                     systemctl daemon-reload && systemctl restart <unit>"
    echo "   (revisá el diff antes: el archivo de /etc puede tener algo a propósito)"
else
    echo "   ✅ todos los units instalados coinciden con el repo"
fi

echo
echo "════════════════════════════════════════════════════════════"
echo " ✅ DEPLOY OK — backend en $DESPUES"
[ "$MOTORES" = "0" ] && echo "    (motores intactos: siguen con su sesión de ROFEX viva)"
echo "════════════════════════════════════════════════════════════"

}

main "$@"
