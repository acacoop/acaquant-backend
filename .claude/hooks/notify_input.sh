#!/usr/bin/env bash
# Notification — avisa cuando Claude necesita tu input/permiso. macOS-only.
set -uo pipefail
[ "$(uname)" = "Darwin" ] || exit 0
MSG="$(jq -r '.message // "Claude te necesita"' 2>/dev/null)"
osascript -e "display notification \"${MSG}\" with title \"Claude Code\" sound name \"Ping\"" >/dev/null 2>&1 || true
exit 0
