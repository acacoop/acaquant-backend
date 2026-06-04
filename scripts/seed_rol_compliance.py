"""scripts/seed_rol_compliance.py — crea el rol `compliance` en Manager.RoleMatrix.

Prod usa Manager.RoleMatrix (la DB pisa el DEFAULT_MATRIX del código), así que un
rol nuevo NO aparece solo en /manager → ROLES Y PERMISOS hasta sembrarlo en la DB.
Este script lo inserta (upsert idempotente, vía set_role_modules → audit + cache
invalidado). Después del run, el rol aparece en la UI y se puede asignar a un user
desde /manager → USUARIOS.

Módulos del rol compliance (decisión 2026-06-04): HOME + todos los mercados +
Manager SOLO Clientes + Compliance (sin `manager` umbrella → no ve tabs admin).

Uso (en el Droplet):
    python -m scripts.seed_rol_compliance
    python -m scripts.seed_rol_compliance --dry-run
"""
from __future__ import annotations

import argparse

from core.roles import MODULES, get_matrix, set_role_modules

_ROLE = "compliance"
_MODS = [
    "home", "renta-fija", "derivados", "agro", "sinteticos",
    "renta-variable", "estrategia",
    "manager_clientes", "manager_compliance",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="no escribe, solo muestra")
    args = ap.parse_args()

    # Chequeo defensivo: todos los módulos deben ser canónicos (set_role_modules
    # filtra los desconocidos en silencio → mejor avisar acá).
    desconocidos = [m for m in _MODS if m not in MODULES]
    if desconocidos:
        print(f"❌ Módulos no canónicos (no están en core.roles.MODULES): {desconocidos}")
        print("   ¿Pulleaste el código con manager_compliance agregado a MODULES?")
        return 1

    actual = get_matrix().get(_ROLE)
    print(f"Rol '{_ROLE}' — antes: {list(actual) if actual else 'NO EXISTE'}")
    print(f"Rol '{_ROLE}' — módulos a setear: {_MODS}")

    if args.dry_run:
        print("\n[DRY-RUN] No se escribió nada.")
        return 0

    after = set_role_modules(role=_ROLE, modules=_MODS, actor="seed_rol_compliance")
    print(f"\n✅ Sembrado. Manager.RoleMatrix['{_ROLE}'].modules = {after.get('modules')}")
    print("→ Ahora aparece en /manager → ROLES Y PERMISOS. Asigná el rol al user "
          "desde /manager → USUARIOS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
