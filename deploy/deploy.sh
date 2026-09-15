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
#      y no toca datos del negocio: los DROP que hay son de vistas que se rehacen
#      abajo y de columnas/tablas que el código dejó de usar, declarados en el
#      mismo archivo. Se puede saltear con --sin-schema.
#   3. restart api.service + agente.service. Los MOTORES no se tocan.
#   4. Smoke — pega a /api/health local y muestra el estado del service.
#
# ⚠️ **LOS MOTORES NO SE REINICIAN** (regla del user, 2026-08-18: «no puedo estar
# reiniciando todos los motores en vivo»). Reiniciar un motor EN RUEDA corta el
# feed de precios de la mesa por varios segundos, y el 95% de los deploys tocan
# la API y no los motores: pagar ese corte en cada deploy es puro costo.
# El script DETECTA si el código nuevo tocó `engines/`, `core/` o `quant/` y lo
# AVISA nombrando los motores activos — pero no los reinicia. Reiniciar en rueda
# es una decisión de la mesa, no un efecto secundario de un deploy.
# Para hacerlo igual: `bash deploy/deploy.sh --con-motores`, o a mano
# `systemctl try-restart motor_X.service` cuando cierre el mercado.
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
CON_MOTORES=0
DETALLE_MOTORES=0
for arg in "$@"; do
    case "$arg" in
        --sin-schema) CON_SCHEMA=0 ;;
        --con-motores) CON_MOTORES=1 ;;
        --motores-detalle) DETALLE_MOTORES=1 ;;
        -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
        *) echo "opción desconocida: $arg (usá --sin-schema, --con-motores, --motores-detalle o --help)"; exit 2 ;;
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

# ⚠️⚠️ **UN DEPLOY PARADO FUERA DE `main` CANTA OK Y NO TRAE NADA.**
#
# Pasó: el server había quedado en `claude/inspiring-cray-sbo0m7`. El `git pull`
# andaba —traía ESA rama—, el service reiniciaba, el smoke daba verde y el script
# imprimía «DEPLOY OK». Pero el código de `main` no llegaba NUNCA, y del otro lado
# la pantalla mostraba el campo nuevo vacío. Cada mitad coherente, nada falla, y
# se lee como «el cambio no funcionó» en vez de «el server está en otra rama».
#
# Se CORTA en vez de avisar: el que corre el deploy quiere el código de main en
# prod, y seguir sería deployar otra cosa con su bendición. El mensaje dice el
# comando exacto, y no vuelve a main solo — esa rama puede tener trabajo que no
# está mergeado, y sacarlo de prod es una decisión, no un efecto secundario.
RAMA="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
if [ "$RAMA" != "main" ]; then
    echo "   rama actual: $RAMA"
    echo
    echo "❌ FALLÓ: el server NO está en main (está en '$RAMA')"
    echo "   Un pull acá trae ESA rama: el deploy diría OK y el código de main"
    echo "   no llegaría nunca."
    echo
    echo "   1) Mirá si esa rama tiene trabajo que main no tenga:"
    echo "        git log --oneline origin/main..$RAMA"
    echo "   2) Si no tiene nada (o ya se mergeó), volvé a main:"
    echo "        git checkout main && git pull --ff-only && bash deploy/deploy.sh"
    exit 1
fi

UPSTREAM="$(git rev-parse --abbrev-ref '@{upstream}' 2>/dev/null || echo '?')"
[ "$UPSTREAM" = "origin/main" ] || echo "   ⚠ main sigue a '$UPSTREAM' (se esperaba origin/main)"

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

# ⚠️⚠️ **EL DAEMON DEL AGENTE NO LO REINICIABA NADIE** (docs/AGENT.md §0.ea).
#
# `agente.service` corre `jobs.agente`, o sea TODOS los detectores. Y no entraba
# por ningún lado: acá se reiniciaba sólo `api.service`, y `restart_all.sh`
# itera `deploy/systemd/motor_*.service` — un glob que `agente.service` no
# matchea. Resultado: después de CUALQUIER deploy la API servía el código nuevo
# y **el agente seguía detectando con el viejo, para siempre**.
#
# El síntoma es cruel porque cada mitad es coherente: los botones nuevos
# aparecen (los sirve la API), y las filas siguen saliendo con el texto viejo
# (las escribe el daemon). Nadie lo ve como «falta un restart» — se ve como
# «el cambio no funcionó». Es la REGLA #9 en el deploy.
#
# **No es un motor y por eso no aplica la regla de no reiniciarlos.** Un motor
# reiniciado en rueda corta el feed de precios de la mesa; el agente no le sirve
# precio a nadie, sólo mira y escribe hallazgos. Y su unit está escrita para que
# el restart sea seguro: atiende SIGTERM, termina la pasada en curso y sale
# limpio. Lo peor que pasa es que se pierda una pasada de detección.
reiniciar_agente() {
    if systemctl list-unit-files agente.service >/dev/null 2>&1; then
        systemctl restart agente.service \
            || echo "   ⚠ agente.service no reinició — 'journalctl -u agente.service -n 50'"
    fi
}

# ── 3. Servicios ────────────────────────────────────────────────────────────
echo
if [ "$CON_MOTORES" = "1" ]; then
    echo "▶ 3/4  restart api.service + MOTORES ACTIVOS (--con-motores)"
    bash deploy/restart_all.sh || morir "restart_all.sh — mirá 'journalctl -u api.service -n 50'"
    reiniciar_agente
else
    echo "▶ 3/4  restart api.service + agente.service (los motores NO se tocan)"
    systemctl restart api.service || morir "systemctl restart api.service — mirá 'journalctl -u api.service -n 50'"
    reiniciar_agente

    # ¿El código nuevo toca a los motores? Se AVISA, no se actúa. Un motor
    # reiniciado en rueda corta el feed de la mesa, y esa es una decisión de
    # ellos — pero enterarse tres días después de que el motor corre código
    # viejo es peor. Por eso: nombrarlo, fuerte, y dejarlo a mano.
    # UNA línea, no un bloque. La versión anterior listaba archivo por archivo y
    # motor por motor con su `systemctl try-restart` al lado, y eso se leía como
    # "reiniciá los motores" — justo lo contrario de lo que el script hace
    # (user, 2026-08-18: «no quiero reiniciar todos los motores por algo que es
    # un git pull y un restart api»). El aviso queda porque enterarse tres días
    # después de que un motor corre código viejo es peor; pero es UNA línea, y
    # el detalle se pide con --motores-detalle.
    if [ "$ANTES" != "$DESPUES" ]; then
        TOCADOS="$(git diff --name-only "$ANTES..$DESPUES" -- engines/ core/ quant/ config.py 2>/dev/null)"
        if [ -n "$TOCADOS" ]; then
            N_ACTIVOS=0
            for unit in deploy/systemd/motor_*.service; do
                systemctl is-active --quiet "$(basename "$unit")" && N_ACTIVOS=$((N_ACTIVOS + 1))
            done
            if [ "$N_ACTIVOS" -gt 0 ]; then
                echo "   ℹ️  tocó código de motores · $N_ACTIVOS activo(s) siguen con el anterior · --con-motores (fuera de rueda)"
            fi
            if [ "$DETALLE_MOTORES" = "1" ]; then
                echo "$TOCADOS" | sed 's/^/        /'
                for unit in deploy/systemd/motor_*.service; do
                    u="$(basename "$unit")"
                    systemctl is-active --quiet "$u" && echo "        systemctl try-restart $u"
                done
            fi
        fi
    fi
fi

# ── 4. Smoke ────────────────────────────────────────────────────────────────
echo
echo "▶ 4/4  smoke"
# La app importa 490 endpoints (numpy/pandas/pyRofex): arrancar tarda MÁS de 3s.
# Con un solo intento el deploy cantaba "la API no levantó" con la API sana y
# sana (pasó el 2026-08-15). Se espera con reintentos hasta 60s y se informa
# cuánto tardó, que además sirve para ver si el arranque se está degradando.
HEALTH=""
for i in $(seq 1 30); do
    HEALTH="$(curl -s --max-time 5 http://localhost:8000/api/health || true)"
    [ -n "$HEALTH" ] && { echo "   /api/health → $HEALTH   (respondió a los $((i * 2))s)"; break; }
    sleep 2
done
if [ -z "$HEALTH" ]; then
    echo "   ⚠️  /api/health no respondió en 60s. Estado del service:"
    systemctl status api.service --no-pager -n 15 || true
    echo "   Últimas líneas del log:"
    journalctl -u api.service -n 25 --no-pager || true
    morir "la API no levantó — el motivo está en el log de arriba"
fi

echo
echo "════════════════════════════════════════════════════════════"
echo " ✅ DEPLOY OK — backend en $DESPUES"
echo "════════════════════════════════════════════════════════════"
