#!/usr/bin/env bash
# Stop — avisa (notificación + sonido) cuando Claude termina, SOLO si el turno
# fue largo (>45s), para no sonar en cada respuesta corta. macOS-only (no-op afuera).
set -uo pipefail
[ "$(uname)" = "Darwin" ] || exit 0
SID="$(jq -r '.session_id // "x"' 2>/dev/null)"
F="/tmp/claude_turn_${SID}"
START="$(cat "$F" 2>/dev/null || echo 0)"; rm -f "$F" 2>/dev/null || true
[ "$START" = "0" ] && exit 0
DUR=$(( $(date +%s) - START ))
[ "$DUR" -lt 45 ] && exit 0
osascript -e "display notification \"Terminó (tras ${DUR}s) — listo para tu input\" with title \"Claude Code\" sound name \"Glass\"" >/dev/null 2>&1 || true
exit 0
