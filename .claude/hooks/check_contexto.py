#!/usr/bin/env python
"""PreToolUse (git push) — el contexto de Claude tiene techo.

Corre `tests/unit/test_contexto_claude.py` antes de cada push y lo BLOQUEA si el
CLAUDE.md raíz superó las 200 líneas / 16 kB, tiene fechas, o una regla de
`.claude/rules/` apunta a un archivo que ya no existe. CI corre el mismo test;
esto es para enterarse ANTES del push, no después.

Mismo contrato que `check_imports.py`: en Python (Windows no tiene bash),
silencioso si no es un push, `allow` con aviso si no hay venv.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
TEST = RAIZ / "tests" / "unit" / "test_contexto_claude.py"
_GIT_PUSH = re.compile(r"\bgit\s+(-C\s+\S+\s+|--no-pager\s+)*(push|pushall)\b")


def _salir(payload: dict | None = None) -> None:
    if payload:
        print(json.dumps(payload))
    sys.exit(0)


def _allow(mensaje: str) -> None:
    _salir({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "allow"},
            "systemMessage": mensaje})


def _deny(motivo: str) -> None:
    _salir({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny",
                                   "permissionDecisionReason": motivo}})


def _comando(payload: dict) -> str:
    entrada = payload.get("tool_input") or {}
    for clave in ("command", "cmd", "script"):
        valor = entrada.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor
    return ""


def _python() -> str:
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python",
                "venv/Scripts/python.exe", "venv/bin/python"):
        if (RAIZ / rel).is_file():
            return str(RAIZ / rel)
    return sys.executable or "python"


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        _salir()
    cmd = _comando(payload if isinstance(payload, dict) else {})
    if not cmd or not _GIT_PUSH.search(cmd) or not TEST.is_file():
        _salir()
    py = _python()
    try:
        sonda = subprocess.run([py, "-c", "import pytest"], cwd=RAIZ, capture_output=True, timeout=30)
        if sonda.returncode != 0:
            # Sin la herramienta NO se bloquea (fallar cerrado por un tool faltante
            # ya bloqueó todos los comandos una vez). CI corre el mismo test.
            _allow("Techo del contexto sin validar: este Python no tiene pytest. CI lo corre igual.")
        r = subprocess.run([py, "-m", "pytest", str(TEST), "-q", "--no-header", "-p", "no:cacheprovider"],
                           cwd=RAIZ, capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError) as e:
        _allow(f"Techo del contexto sin validar (no pude correr pytest: {e}). CI lo corre igual.")
    if r.returncode == 0:
        _salir()
    salida = (r.stdout or r.stderr or "").strip()
    _deny("PUSH BLOQUEADO — el contexto de Claude superó su techo (o una regla quedó muerta). "
          "Movelo a .claude/rules/<dominio>.md o a docs/, no achiques la letra.\n\n" + salida[-2500:])


if __name__ == "__main__":
    main()
