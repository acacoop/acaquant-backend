"""Genera un requirements.txt pineado a las versiones EXACTAS instaladas.

Por qué: `requirements.txt` no tenía ningún pin (`==`) — cada `pip install`
podía traer una versión nueva incompatible y tumbar el boot al `restart`
(EXT-DEP1 de docs/SECURITY_AUDIT_2026-05.md). Este script fotografía el
entorno actual (known-good, prod está corriendo con él) y deja todo clavado.

Estrategia: NO resuelve contra PyPI (eso pinearía a la última, que es justo
el riesgo). Lee lo que YA está instalado en el venv y lo pinea. La próxima
`pip install -r requirements.txt` queda determinística y no upgradea nada.

Salida: reescribe `requirements.txt` con dos bloques —
  1. Deps directas (las que estaban listadas), pineadas, con sus comentarios.
  2. Transitivas, pineadas, bajo un separador.

Uso (en el Droplet, dentro del venv):
    python -m scripts.lock_deps          # reescribe requirements.txt
    python -m scripts.lock_deps --check  # solo reporta, no escribe (CI/dry-run)

Después: git add requirements.txt && commit && push. Para actualizar una dep
en el futuro: editar el pin a mano (o desinstalar/instalar la nueva) y volver
a correr este script.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REQ_PATH = Path(__file__).resolve().parent.parent / "requirements.txt"

# Herramientas de bootstrap del venv: NO pinearlas (se manejan aparte y
# pinearlas puede romper el propio `pip install`).
_SKIP = {"pip", "setuptools", "wheel", "distribute", "pkg-resources"}

HEADER = """\
# requirements.txt — PINEADO. Generado por `python -m scripts.lock_deps`.
# No editar a mano salvo para bumpear un pin puntual. Las versiones reflejan
# lo que estaba instalado en el venv al momento de generar (known-good).
# Para actualizar: instalar la versión nueva en el venv y re-correr el script.
"""


def _canon(name: str) -> str:
    """Nombre canónico PEP 503 para matchear (case/`-_.`-insensitive)."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _parse_direct(req_text: str) -> list[tuple[str, str, str]]:
    """Devuelve [(nombre_base, spec_original, comentario)] de las deps directas.

    `spec_original` preserva extras (ej. `uvicorn[standard]`). Ignora líneas
    en blanco, comentarios sueltos y el separador de transitivas.
    """
    out: list[tuple[str, str, str]] = []
    for raw in req_text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        # separa comentario inline
        if "#" in line:
            spec_part, comment = line.split("#", 1)
            comment = comment.strip()
        else:
            spec_part, comment = line, ""
        spec = spec_part.strip()
        if not spec:
            continue
        # nombre base = antes de [extras] o de cualquier ==/>=
        base = re.split(r"[\[<>=!~ ]", spec, maxsplit=1)[0]
        # spec sin la versión pineada previa (re-pineamos nosotros)
        spec_no_ver = re.split(r"[<>=!~]=?", spec, maxsplit=1)[0].strip()
        out.append((base, spec_no_ver, comment))
    return out


def _installed_versions() -> dict[str, str]:
    """Mapa {nombre_canónico: versión} de todo lo instalado (pip freeze)."""
    res = subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--all"],
        capture_output=True,
        text=True,
        check=True,
    )
    versions: dict[str, str] = {}
    for line in res.stdout.splitlines():
        if "==" not in line or line.startswith("#") or line.startswith("-e"):
            continue
        name, ver = line.split("==", 1)
        canon = _canon(name)
        if canon in _SKIP:
            continue
        versions[canon] = ver.strip()
    return versions


def main() -> int:
    check_only = "--check" in sys.argv

    if not REQ_PATH.exists():
        print(f"ERROR: no encuentro {REQ_PATH}", file=sys.stderr)
        return 1

    direct = _parse_direct(REQ_PATH.read_text(encoding="utf-8"))
    installed = _installed_versions()

    direct_canon = {_canon(base) for base, _, _ in direct}
    missing = [base for base, _, _ in direct if _canon(base) not in installed]
    if missing:
        print(
            "ERROR: estas deps directas no están instaladas en este venv: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        print("Instalalas primero (o corré el script en el venv de prod).", file=sys.stderr)
        return 1

    lines: list[str] = [HEADER, "# --- deps directas ---"]
    for base, spec_no_ver, comment in direct:
        ver = installed[_canon(base)]
        pin = f"{spec_no_ver}=={ver}"
        lines.append(f"{pin}  # {comment}" if comment else pin)

    transitive = sorted(
        (name, ver)
        for name, ver in installed.items()
        if name not in direct_canon
    )
    lines.append("")
    lines.append("# --- transitivas (pineadas automáticamente) ---")
    lines.extend(f"{name}=={ver}" for name, ver in transitive)
    lines.append("")

    new_text = "\n".join(lines)

    if check_only:
        print(f"[--check] {len(direct)} directas + {len(transitive)} transitivas")
        print("No se escribió nada (--check).")
        return 0

    REQ_PATH.write_text(new_text, encoding="utf-8")
    print(
        f"OK: {REQ_PATH.name} pineado — "
        f"{len(direct)} directas + {len(transitive)} transitivas."
    )
    print("Revisá el diff, después: git add requirements.txt && commit && push.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
