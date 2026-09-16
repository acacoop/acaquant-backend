#!/usr/bin/env bash
# PreToolUse hook (git push) — valida la REGLA #1 del CLAUDE.md.
#
# Un import roto en CUALQUIER router/service montado en api/main.py tumba
# TODA la API (proceso único) → 502 en toda la web tras el deploy. Este
# hook corre `from api.main import app` antes de cada `git push` y lo
# BLOQUEA si no importa.
#
# Degradación prolija:
#   - No es el repo AcaQuant (sin api/main.py) → allow silencioso.
#   - El venv no tiene las deps (ni fastapi importa) → no se puede validar
#     → allow + aviso (instalar deps).
set -uo pipefail

emit_allow()     { jq -nc '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"allow"}}'; exit 0; }
emit_allow_msg() { jq -nc --arg m "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"allow"},systemMessage:$m}'; exit 0; }
emit_deny()      { jq -nc --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'; exit 0; }

[ -f "api/main.py" ] || emit_allow

PY=".venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || emit_allow_msg "REGLA #1 sin validar: no encontré python."

if ! "$PY" -c "import fastapi" 2>/dev/null; then
  emit_allow_msg "REGLA #1 sin validar: el venv no tiene las deps. Corré '.venv/bin/pip install -r requirements.txt' para que el push se valide solo."
fi

if ERR="$("$PY" -c "from api.main import app" 2>&1)"; then
  emit_allow
else
  REASON="$(printf '%s' "$ERR" | tail -4)"
  emit_deny "REGLA #1 — api.main NO importa: este push tumbaría toda la API. Arreglá el import antes de pushear. >>> $REASON"
fi
