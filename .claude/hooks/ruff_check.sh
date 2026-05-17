#!/usr/bin/env bash
# PostToolUse hook (Write|Edit) — corre `ruff check` sobre el archivo .py
# que Claude acaba de escribir/editar. Informativo: imprime los hallazgos
# para verlos al instante, NO bloquea.
set -uo pipefail

F="$(jq -r '.tool_input.file_path // .tool_response.filePath // empty' 2>/dev/null)"
case "$F" in
  *.py) ;;
  *) exit 0 ;;
esac
[ -f "$F" ] || exit 0

RUFF=".venv/bin/ruff"
[ -x "$RUFF" ] || RUFF="$(command -v ruff || true)"
[ -n "$RUFF" ] || exit 0

if ! OUT="$("$RUFF" check "$F" 2>&1)"; then
  echo "ruff check — $F"
  echo "$OUT"
fi
exit 0
