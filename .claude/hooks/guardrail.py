#!/usr/bin/env python
"""PreToolUse — guardrail de comandos catastróficos (port de guardrail.sh).

Está en Python y no en bash porque el hook tiene que correr en los DOS entornos:
Linux/Claude Code (que trae bash) y Windows/VS Code (que no tiene ni `bash` ni
`jq`). Cuando el hook no se puede ejecutar, el runtime falla-cerrado y bloquea
TODOS los comandos — que es exactamente lo que pasó el 2026-08-07.

BLOQUEA si matchea; si no, sale silencioso (NO emite "allow" → el flujo de
permisos normal sigue). Solo inspecciona el comando literal: los scripts del
repo (`python -m scripts.x`) son código revisado y no se tocan acá.

Diseño anti-falsos-positivos: los patrones de borrado masivo solo disparan si el
comando INVOCA un ejecutor (python/mongosh/node), así un `grep dropDatabase` o
un `git log | grep delete_many` (búsquedas) pasan.
"""
from __future__ import annotations

import json
import re
import sys

# Ejecutores: sin uno de estos, una mención al patrón es texto, no una ejecución.
_EXEC = re.compile(r"\b(python|python3|mongosh|mongo|node|eval)\b", re.I)

_REGLAS: list[tuple[re.Pattern[str], bool, str]] = [
    # (patrón, exige_ejecutor, motivo)
    (re.compile(r"dropDatabase|drop_database", re.I), True,
     "dropDatabase bloqueado. Si es intencional, hacelo desde la UI de la base "
     "o con un script revisado en scripts/."),
    (re.compile(r"(delete_many|deleteMany|remove)\s*\(\s*\{\s*\}\s*\)"), True,
     "borrado de TODA una colección (filtro vacío {}) bloqueado. Filtrá, o usá "
     "un script revisado."),
    # rm -rf sobre path de sistema/home (no subdirectorios del proyecto).
    (re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*-?[a-zA-Z]*[rf][a-zA-Z]*\s+"
                r"(/|~|\$HOME|/Users|/etc|/var|/usr|/bin)(\s|/|$)"), False,
     "'rm -rf' sobre path de sistema/home bloqueado. Borrá rutas relativas "
     "dentro del proyecto."),
    # Equivalente Windows: borrado recursivo de una raíz o del perfil del usuario.
    (re.compile(r"Remove-Item[^\n|;]*-Recurse", re.I), False,
     "borrado recursivo sobre una raíz o el perfil del usuario bloqueado. "
     "Borrá rutas relativas dentro del proyecto.", ),
    (re.compile(r"\b(Format-Volume|Clear-Disk)\b", re.I), False,
     "formateo de disco bloqueado."),
    (re.compile(r":\(\)\s*\{\s*:"), False, "fork-bomb bloqueada."),
    (re.compile(r"(^|[;&|]\s*)(mkfs|dd)\b.*(of=/dev/|/dev/sd|/dev/disk)"), False,
     "formateo / escritura directa a disco bloqueado."),
]

# El borrado recursivo en Windows solo es catastrófico sobre estos targets; sobre
# una carpeta del proyecto es rutina.
_WIN_RM_PELIGROSO = re.compile(
    r"-Recurse[^\n|;]*?((\b[A-Za-z]:\\?(\s|$|['\"]))|\$env:USERPROFILE\s*['\"]?\s*$"
    r"|\$HOME\s*['\"]?\s*$|\\Users\\?(\s|$)|\\Windows\b)", re.I)


def _comando(payload: dict) -> str:
    """El comando literal, sin depender del nombre exacto del campo por runtime."""
    entrada = payload.get("tool_input") or {}
    for clave in ("command", "cmd", "script"):
        valor = entrada.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor
    return ""


def _deny(motivo: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": f"GUARDRAIL: {motivo}",
    }}))
    sys.exit(0)


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        return  # Sin payload legible no hay nada que inspeccionar: no bloqueamos.
    cmd = _comando(payload if isinstance(payload, dict) else {})
    if not cmd:
        return
    hay_ejecutor = bool(_EXEC.search(cmd))
    for patron, exige_ejecutor, motivo, *_ in _REGLAS:
        if exige_ejecutor and not hay_ejecutor:
            continue
        if not patron.search(cmd):
            continue
        if patron.pattern.startswith("Remove-Item") and not _WIN_RM_PELIGROSO.search(cmd):
            continue
        _deny(motivo)


if __name__ == "__main__":
    main()
