#!/usr/bin/env python
"""PreToolUse (git push) — valida la REGLA #1 del CLAUDE.md.

Un import roto en CUALQUIER router/service montado en api/main.py tumba TODA la
API (proceso único) → 502 en toda la web tras el deploy. Este hook corre
`from api.main import app` antes de cada `git push` y lo BLOQUEA si no importa.

Está en Python (no en bash) para que corra igual en Linux/Claude Code y en
Windows/VS Code — ver el docstring de guardrail.py. La condición "solo en git
push" vive ACÁ y no en settings.json: el campo `if` no es parte del schema de
hooks y hacía fallar el bloque entero.

Degradación prolija:
  - El comando no es un `git push`         → allow silencioso.
  - No es el repo TradingAV (sin api/main) → allow silencioso.
  - El venv no tiene las deps              → allow + aviso (instalar deps).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
_GIT_PUSH = re.compile(r"\bgit\s+(-C\s+\S+\s+|--no-pager\s+)*(push|pushall)\b")


def _salir(payload: dict | None = None) -> None:
    if payload:
        print(json.dumps(payload))
    sys.exit(0)


def _allow(mensaje: str = "") -> None:
    salida: dict = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                           "permissionDecision": "allow"}}
    if mensaje:
        salida["systemMessage"] = mensaje
    _salir(salida)


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
    """El intérprete del venv si está; si no, el que corre este hook."""
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python",
                "venv/Scripts/python.exe", "venv/bin/python"):
        candidato = RAIZ / rel
        if candidato.is_file():
            return str(candidato)
    return sys.executable or "python"


def _corre(py: str, codigo: str) -> tuple[bool, str]:
    try:
        r = subprocess.run([py, "-c", codigo], cwd=RAIZ, capture_output=True,
                           text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return r.returncode == 0, (r.stderr or r.stdout or "").strip()


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        _salir()
    cmd = _comando(payload if isinstance(payload, dict) else {})
    if not cmd or not _GIT_PUSH.search(cmd):
        _salir()                      # No es un push: no opinamos.
    if not (RAIZ / "api" / "main.py").is_file():
        _salir()                      # Otro repo (ej. el frontend).

    py = _python()
    if not _corre(py, "import fastapi")[0]:
        _allow("REGLA #1 sin validar: el venv no tiene las deps. Corré "
               "'pip install -r requirements.txt' para que el push se valide solo.")

    ok, err = _corre(py, "from api.main import app")
    if ok:
        _allow()
    _deny("REGLA #1 — api.main NO importa: este push tumbaría toda la API. "
          "Arreglá el import antes de pushear. >>> "
          + "\n".join(err.splitlines()[-4:]))


if __name__ == "__main__":
    main()
