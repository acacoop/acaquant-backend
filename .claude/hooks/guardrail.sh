#!/usr/bin/env bash
# PreToolUse (Bash) — guardrail de comandos catastróficos que el matcher de
# permisos no captura. BLOQUEA si matchea; si no, sale silencioso (NO emite
# "allow" → el flujo de permisos normal sigue). Solo inspecciona el comando
# Bash literal; los scripts del repo (python -m scripts.x) que internamente
# hacen drop() son código revisado y no se tocan acá.
#
# Diseño anti-falsos-positivos: los patrones de Mongo (dropDatabase / borrado
# masivo) solo disparan si el comando INVOCA un ejecutor (python/mongosh/node).
# Así un `grep dropDatabase` o `git log | grep delete_many` (búsquedas) pasan.
set -uo pipefail

CMD="$(jq -r '.tool_input.command // empty' 2>/dev/null)"
[ -n "$CMD" ] || exit 0

emit_deny() { jq -nc --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'; exit 0; }
has()  { printf '%s' "$CMD" | grep -qiE "$1"; }
hasx() { printf '%s' "$CMD" | grep -qE "$1"; }
is_exec() { printf '%s' "$CMD" | grep -qE '\b(python|python3|mongosh|mongo|node|eval)\b'; }

# Mongo: drop de base / borrado con filtro vacío — solo si se está EJECUTANDO.
if is_exec && has 'dropDatabase|drop_database'; then
  emit_deny "GUARDRAIL: dropDatabase bloqueado. Si es intencional, hacelo desde la Atlas UI o un script revisado en scripts/."
fi
if is_exec && hasx '(delete_many|deleteMany|remove)\s*\(\s*\{\s*\}\s*\)'; then
  emit_deny "GUARDRAIL: borrado de TODA una colección (filtro vacío {}) bloqueado. Filtrá, o usá un script revisado."
fi

# rm -rf sobre path de sistema/home (no subdirectorios del proyecto).
if hasx '\brm\s+(-[a-zA-Z]*\s+)*-?[a-zA-Z]*[rf][a-zA-Z]*\s+(/|~|\$HOME|/Users|/etc|/var|/usr|/bin)(\s|/|$)'; then
  emit_deny "GUARDRAIL: 'rm -rf' sobre path de sistema/home bloqueado. Borrá rutas relativas dentro del proyecto."
fi

# Fork bomb, o formateo/escritura a disco como COMANDO (inicio o tras ;&|),
# no como mención en un texto/mensaje.
if hasx ':\(\)\s*\{\s*:' || hasx '(^|[;&|]\s*)(mkfs|dd)\b' && hasx 'of=/dev/|/dev/sd|/dev/disk'; then
  emit_deny "GUARDRAIL: comando catastrófico (fork-bomb / formateo / escritura a disco) bloqueado."
fi

exit 0
