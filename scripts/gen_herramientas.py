"""gen_herramientas.py — genera/actualiza docs/HERRAMIENTAS.md desde scripts/.

Auto-mantenible: cada script reusable se auto-declara con UNA línea en su docstring:

    Herramienta: <categoría> · <descripción ejecutiva en una línea>

Este generador escanea scripts/*.py, junta esas líneas y regenera la tabla de
docs/HERRAMIENTAS.md (entre los marcadores AUTOGEN). Agregás un script con el
marcador → aparece; lo borrás → desaparece. La narrativa fuera de los marcadores
se mantiene a mano.

    python -m scripts.gen_herramientas          # regenera la tabla
    python -m scripts.gen_herramientas --check  # exit!=0 si quedó desincronizado

Herramienta: infra · Regenera docs/HERRAMIENTAS.md (catálogo de herramientas) desde los docstrings.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
DOC = ROOT / "docs" / "HERRAMIENTAS.md"

_MARK = re.compile(r"^Herramienta:\s*(\S+)\s*·\s*(.+?)\s*$", re.MULTILINE)
_START = "<!-- AUTOGEN:START — generado por scripts.gen_herramientas, no editar a mano -->"
_END = "<!-- AUTOGEN:END -->"

_ORDEN = ["dba", "perf", "infra"]  # categorías conocidas primero; el resto, alfabético

_NARRATIVA = """\
# Herramientas — auditoría, performance y mantenimiento

Catálogo de los scripts **reusables** del repo (los que se corren cada tanto, no
los one-shot). Cada uno es read-only o trae su propio `--dry-run`; todos se
invocan con `python -m scripts.<nombre>` desde la raíz.

> Esta tabla se genera sola: cada herramienta se declara con una línea
> `Herramienta: <categoría> · <descripción>` en su docstring. Para regenerar:
> `python -m scripts.gen_herramientas`. No editar entre los marcadores AUTOGEN.

"""


def _scan() -> dict[str, list[tuple[str, str]]]:
    cats: dict[str, list[tuple[str, str]]] = {}
    for f in sorted(SCRIPTS.glob("*.py")):
        if f.name == "__init__.py":
            continue
        txt = f.read_text(encoding="utf-8", errors="ignore")[:4000]
        m = _MARK.search(txt)
        if not m:
            continue
        cat, desc = m.group(1).lower(), m.group(2)
        cats.setdefault(cat, []).append((f.stem, desc))
    return cats


def _render(cats: dict[str, list[tuple[str, str]]]) -> str:
    orden = [c for c in _ORDEN if c in cats] + sorted(c for c in cats if c not in _ORDEN)
    titulos = {"dba": "DBA / base de datos", "perf": "Performance / profiling", "infra": "Infra / sincronización"}
    out = [_START, ""]
    total = sum(len(v) for v in cats.values())
    out.append(f"*{total} herramientas en {len(orden)} categorías.*\n")
    for cat in orden:
        out.append(f"### {titulos.get(cat, cat)}")
        out.append("")
        out.append("| herramienta | qué hace |")
        out.append("|---|---|")
        for name, desc in sorted(cats[cat]):
            out.append(f"| `python -m scripts.{name}` | {desc} |")
        out.append("")
    out.append(_END)
    return "\n".join(out)


def _compose(block: str) -> str:
    if DOC.exists():
        cur = DOC.read_text(encoding="utf-8")
        if _START in cur and _END in cur:
            pre = cur.split(_START)[0]
            post = cur.split(_END, 1)[1]
            return pre.rstrip() + "\n\n" + block + post
    return _NARRATIVA + block + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Regenera docs/HERRAMIENTAS.md")
    ap.add_argument("--check", action="store_true", help="falla si está desincronizado")
    args = ap.parse_args()

    nuevo = _compose(_render(_scan()))
    actual = DOC.read_text(encoding="utf-8") if DOC.exists() else ""

    if args.check:
        if nuevo.strip() != actual.strip():
            print("docs/HERRAMIENTAS.md desincronizado — correr: python -m scripts.gen_herramientas")
            sys.exit(1)
        print("HERRAMIENTAS.md OK")
        return

    DOC.parent.mkdir(exist_ok=True)
    DOC.write_text(nuevo, encoding="utf-8")
    print(f"Actualizado {DOC.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
