#!/usr/bin/env python
"""PreToolUse (git push) — los tests del AV AGENT corren antes de que salga un
push que lo toque. REGLA #10 y `.claude/skills/add-habilidad.md`.

El 2026-09-02 se sumaron cuatro habilidades y dos veces faltó una pieza (la
cita del diario, la `Pieza` del árbol de diagnóstico para un cron nuevo). Las
dos veces lo dijo un test — después de pushear a `main`. Este hook corre esos
tests ANTES, y solo cuando el diff contra `origin/main` toca lo que el agente
congela: `agente/`, `docs/AGENT.md`, el registro de diagnóstico, el crontab,
los units de systemd o `core/latido.py`.

Misma degradación prolija que `check_imports.py`: no es un push → allow; no es
este repo → allow; sin deps → allow con aviso. Un test rojo → deny con la
última pantalla de pytest.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
_GIT_PUSH = re.compile(r"\bgit\s+(-C\s+\S+\s+|--no-pager\s+)*(push|pushall)\b")
_TOCA = ("agente/", "docs/AGENT.md", "api/services/diagnostico_registry.py",
         "deploy/crontab.txt", "deploy/systemd/", "core/latido.py",
         "tests/unit/test_agente.py")
_TESTS = ("tests/unit/test_agente.py", "tests/unit/test_doc_agente.py",
          "tests/test_diagnostico_registry.py", "tests/unit/test_scripts.py")


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
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python",
                "venv/Scripts/python.exe", "venv/bin/python"):
        if (RAIZ / rel).is_file():
            return str(RAIZ / rel)
    return sys.executable or "python"


def _archivos_tocados() -> list[str]:
    """Lo que este push lleva: el diff contra origin/main más lo sin commitear."""
    out: set[str] = set()
    for args in (["git", "diff", "--name-only", "origin/main...HEAD"],
                 ["git", "diff", "--name-only", "HEAD"],
                 ["git", "diff", "--name-only", "--cached"]):
        try:
            r = subprocess.run(args, cwd=RAIZ, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0:
            out.update(x.strip() for x in r.stdout.splitlines() if x.strip())
    return sorted(out)


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        _salir()
    cmd = _comando(payload if isinstance(payload, dict) else {})
    if not cmd or not _GIT_PUSH.search(cmd):
        _salir()
    if not (RAIZ / "agente" / "catalogo.py").is_file():
        _salir()

    tocados = [a for a in _archivos_tocados() if a.startswith(_TOCA)]
    if not tocados:
        _salir()                      # el push no toca al agente

    py = _python()
    try:
        r = subprocess.run([py, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *_TESTS],
                           cwd=RAIZ, capture_output=True, text=True, timeout=170)
    except (OSError, subprocess.SubprocessError) as e:
        _allow(f"tests del agente sin correr ({e}): el push sale sin validar.")
    if r.returncode == 0:
        _allow()
    if "No module named pytest" in (r.stderr or ""):
        _allow("tests del agente sin correr: el venv no tiene pytest.")
    cola = "\n".join((r.stdout or r.stderr or "").splitlines()[-12:])
    _deny("REGLA #10 — el push toca al AV AGENT y sus tests están en ROJO. "
          f"Tocados: {', '.join(tocados[:6])}. Arreglalo antes de pushear. >>> {cola}")


if __name__ == "__main__":
    main()
