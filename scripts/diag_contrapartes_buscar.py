"""scripts/diag_contrapartes_buscar.py "<texto>" — corre la búsqueda REAL del
backend (api.services.contrapartes_seg.listar_contrapartes) y muestra qué devuelve.

Sirve para aislar si el "buscador trae cosas sin sentido" es un bug del BACKEND
(la query devuelve mal) o del FRONTEND (resultados viejos por cache/deploy). Si
acá devuelve lo correcto, el problema es cache del front (hard refresh / deploy).

Uso (en el Droplet):
    python -m scripts.diag_contrapartes_buscar "70"
    python -m scripts.diag_contrapartes_buscar "nicolas mollo"
"""
from __future__ import annotations

import sys

from api.services import contrapartes_seg as m


def main() -> int:
    q = " ".join(sys.argv[1:]).strip() or None
    res = m.listar_contrapartes(q=q)
    print(f"query={q!r}  →  {res['n']} resultado(s)\n")
    for c in res["contrapartes"][:40]:
        print(f"  {c['cuenta']!s:>7}  {c['denominacion']}")
    if res["n"] > 40:
        print(f"  … y {res['n'] - 40} más")
    print("\nLISTO (read-only). Si esto trae lo correcto, el bug es cache/deploy del front.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
