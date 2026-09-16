#!/usr/bin/env bash
# PostToolUse (Write|Edit) — docs/ACAQUANT.md, EL doc oficial, nunca queda viejo:
#   - Se editó el doc a mano        → lo re-estampa solo (fecha + huella). Silencioso.
#   - systemd unit / crontab / schema.sql → avisa si las tablas del doc quedaron
#     desincronizadas (scripts.gen_sistema --check). Informativo, no bloquea.
set -uo pipefail

F="$(jq -r '.tool_input.file_path // .tool_response.filePath // empty' 2>/dev/null)"

PY=".venv/bin/python"
[ -x "$PY" ] || PY=".venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$(command -v python || command -v python3 || true)"
[ -n "$PY" ] || exit 0

case "$F" in
  *docs/ACAQUANT.md)
    "$PY" -m scripts.gen_sistema >/dev/null 2>&1 || echo "⚠️  No pude re-estampar docs/ACAQUANT.md: corré python -m scripts.gen_sistema"
    ;;
  *systemd*.service|*crontab.txt|*sql/schema.sql)
    if ! "$PY" -m scripts.gen_sistema --check >/dev/null 2>&1; then
      echo "⚠️  Tocaste la fuente de docs/ACAQUANT.md (systemd/crontab/schema) y sus tablas quedaron viejas."
      echo "    Regenerá: python -m scripts.gen_sistema   (o el skill /sistema)"
    fi
    ;;
esac
exit 0
