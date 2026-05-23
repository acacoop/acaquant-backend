#!/usr/bin/env bash
# PostToolUse (Write|Edit) — si se editó un systemd unit o el crontab,
# verifica que el plano (deploy/SISTEMA.md) siga sincronizado con la fuente
# real. Informativo (no bloquea): recuerda regenerar si quedó desfasado.
set -uo pipefail

F="$(jq -r '.tool_input.file_path // .tool_response.filePath // empty' 2>/dev/null)"
case "$F" in
  *systemd*.service|*crontab.txt) ;;
  *) exit 0 ;;
esac

PY=".venv/bin/python"
[ -x "$PY" ] || PY=".venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$(command -v python || command -v python3 || true)"
[ -n "$PY" ] || exit 0

if ! "$PY" -m scripts.gen_sistema --check >/dev/null 2>&1; then
  echo "⚠️  Tocaste deploy/ (systemd/crontab) y deploy/SISTEMA.md quedó desincronizado."
  echo "    Regenerá el plano: python -m scripts.gen_sistema   (o el skill /sistema)"
fi
exit 0
