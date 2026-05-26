#!/usr/bin/env bash
# UserPromptSubmit — marca el inicio del turno (para medir duración en Stop).
set -uo pipefail
SID="$(jq -r '.session_id // "x"' 2>/dev/null)"
date +%s > "/tmp/claude_turn_${SID}" 2>/dev/null || true
exit 0
