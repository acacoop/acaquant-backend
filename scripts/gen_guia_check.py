"""gen_guia_check.py — ¿el mapa del GUÍA cubre lo que el frontend tiene HOY?

El mapa de la vista `ayuda` (copiloto/ayuda.py) es curado a mano → se pudre en
silencio cuando el frontend agrega/mueve pestañas (caso real 2026-07-20: el
filtro SOLO GAR de Back Office y la mudanza de pestañas de /operaciones no
estaban, y el guía inventaba). Mismo contrato que gen_sistema --check: este
script extrae del CÓDIGO del frontend (../acaquant-web) las rutas del menú y
los labels de TODAS las pestañas (TabBtn), y avisa cuáles NO aparecen en el
mapa del guía.

Uso (local, con el checkout hermano de acaquant-web):
    python -m scripts.gen_guia_check           # reporte de faltantes
    python -m scripts.gen_guia_check --strict  # exit 1 si falta algo (CI/hook)

Es un REPORTE para humanos: algún label puede ser un sub-panel interno que no
amerita el mapa — el criterio final es tuyo. Pero cada faltante es una pregunta
que el guía va a responder MAL hasta que lo agregues.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

from api.services.copiloto.ayuda import _MAPA

# Consolas Windows cp1252: un ✓ en un print crashea el script (feed.py docet).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

FRONT = Path(__file__).resolve().parent.parent.parent / "acaquant-web" / "src"

# Labels que son sub-paneles/controles internos, no destinos navegables — no
# ameritan fila en el mapa (revisar esta lista al agregar excepciones).
_IGNORAR = {"PAYOFF", "ESCENARIOS", "PIVOTES", "VOLUMENES ACCIONES", "RENTA FIJA",
            "MOVERS", "DATOS", "CI", "24HS", "TODO"}

_RE_TAB_INLINE = re.compile(r">([A-ZÁÉÍÓÚÑ][^<>{}]{1,40})</TabBtn>")
_RE_TAB_MULTI = re.compile(r"<TabBtn[^>]*>\s*\n\s*([A-ZÁÉÍÓÚÑa-z][^<>{}\n]{1,40})\s*\n\s*</TabBtn>")
_RE_NAV = re.compile(r'href:\s*"(/[a-z-]*)",\s*label:\s*"([^"]+)"')


def _norm(s: str) -> str:
    """Mayúsculas sin tildes — para comparar labels contra el texto del mapa."""
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn").upper().strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="exit 1 si hay faltantes")
    args = ap.parse_args()

    if not FRONT.exists():
        sys.exit(f"[ERROR] no encuentro el frontend en {FRONT} (checkout hermano)")

    # 1. Todo lo navegable del frontend: rutas del NAV + labels de pestañas
    encontrados: dict[str, str] = {}   # label normalizado → dónde se vio
    for f in FRONT.rglob("*.tsx"):
        src = f.read_text(encoding="utf-8", errors="replace")
        for rx in (_RE_TAB_INLINE, _RE_TAB_MULTI):
            for m in rx.finditer(src):
                lbl = _norm(m.group(1))
                if lbl and lbl not in _IGNORAR and not lbl.startswith("{"):
                    encontrados.setdefault(lbl, f.name)
        if f.name == "header.tsx":
            for m in _RE_NAV.finditer(src):
                encontrados.setdefault(_norm(m.group(2)), "header.tsx (menú)")

    # 2. El texto completo del mapa del guía, normalizado
    mapa_txt = _norm(" ".join(
        f"{r.get('seccion', '')} {r.get('menu', '')} {r.get('que_hay', '')}" for r in _MAPA))

    faltan = {lbl: origen for lbl, origen in sorted(encontrados.items())
              if lbl not in mapa_txt}

    print(f"frontend: {len(encontrados)} destinos navegables (menú + pestañas)")
    print(f"mapa del guía: {len(_MAPA)} filas")
    if not faltan:
        print("✓ el mapa cubre todo lo navegable que el frontend expone hoy.")
        return
    print(f"\n⚠ {len(faltan)} destino(s) del frontend NO aparecen en el mapa del guía:")
    for lbl, origen in faltan.items():
        print(f"  - {lbl!r:42} (visto en {origen})")
    print("\n→ cada uno es una pregunta que el guía va a responder MAL: sumalo al mapa "
          "(copiloto/ayuda.py) o a _IGNORAR si es un control interno.")
    if args.strict:
        sys.exit(1)


if __name__ == "__main__":
    main()
