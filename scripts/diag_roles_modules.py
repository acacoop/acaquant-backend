"""scripts/diag_roles_modules.py — qué módulos/matriz ve el código que está
corriendo (read-only). Para depurar por qué `manager_contrapartes` no aparece en
el panel ROLES Y PERMISOS.

El panel se puebla con `MODULES` (vía GET /api/manager/roles). Si acá aparece
`manager_contrapartes`, el código está actualizado → si igual no se ve en el panel,
el proceso de la API no se reinició con el código nuevo (o hay cache del proxy).

Uso (en el Droplet):
    python -m scripts.diag_roles_modules
"""
from __future__ import annotations

from core.roles import MODULES, get_matrix


def main() -> int:
    print(f"total MODULES: {len(MODULES)}")
    print(f"manager_contrapartes en MODULES: {'manager_contrapartes' in MODULES}")
    print("\nMatriz (get_matrix — lo que devuelve el endpoint /api/manager/roles):")
    m = get_matrix()
    print(f"  roles: {list(m.keys())}")
    for role, mods in m.items():
        flag = "✓" if "manager_contrapartes" in mods else " "
        print(f"  [{flag}] {role}: {len(mods)} módulos")
    print("\nLISTO. Si MODULES=18 y aparece manager_contrapartes pero el panel no lo "
          "muestra → la API NO levantó este código (revisar el restart) o cache del proxy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
