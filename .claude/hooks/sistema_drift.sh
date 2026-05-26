#!/usr/bin/env bash
# PostToolUse (Write|Edit) — drift de docs AUTOGENERADOS:
#   - systemd unit / crontab → deploy/SISTEMA.md      (scripts.gen_sistema)
#   - scripts/*.py           → docs/HERRAMIENTAS.md    (scripts.gen_herramientas)
# Informativo (no bloquea): recuerda regenerar si el doc quedó desfasado de su
# fuente real. El --check solo avisa cuando DE VERDAD difiere (bajo ruido).
set -uo pipefail

F="$(jq -r '.tool_input.file_path // .tool_response.filePath // empty' 2>/dev/null)"

PY=".venv/bin/python"
[ -x "$PY" ] || PY=".venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$(command -v python || command -v python3 || true)"
[ -n "$PY" ] || exit 0

case "$F" in
  *systemd*.service|*crontab.txt)
    if ! "$PY" -m scripts.gen_sistema --check >/dev/null 2>&1; then
      echo "⚠️  Tocaste deploy/ (systemd/crontab) y deploy/SISTEMA.md quedó desincronizado."
      echo "    Regenerá: python -m scripts.gen_sistema   (o el skill /sistema)"
    fi
    ;;
  */scripts/*.py)
    if ! "$PY" -m scripts.gen_herramientas --check >/dev/null 2>&1; then
      echo "⚠️  Cambió una herramienta y docs/HERRAMIENTAS.md quedó desincronizado."
      echo "    Regenerá: python -m scripts.gen_herramientas"
    fi
    ;;
esac
exit 0
