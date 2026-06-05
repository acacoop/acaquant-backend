"""Otorga el módulo `manager_titulos` al rol asistente_comercial en Manager.RoleMatrix.

La RoleMatrix de prod PISA el DEFAULT_MATRIX del código → sumar el módulo al
código (core/roles.py) NO alcanza para que el rol lo tenga en vivo. Este script
lo agrega a la matriz viva, preservando los módulos que ya tiene. Idempotente.

Uso:
    python -m scripts.grant_manager_titulos            # aplica
    python -m scripts.grant_manager_titulos --dry-run  # muestra, no escribe
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client
from core.roles import set_role_modules

ROL = "asistente_comercial"
MODULO = "manager_titulos"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="No escribe; solo muestra.")
    args = ap.parse_args()

    col = get_mongo_client()["Manager"]["RoleMatrix"]
    doc = col.find_one({"role": ROL}, {"_id": 0, "modules": 1})
    if not doc:
        print(f"El rol {ROL!r} NO está en Manager.RoleMatrix → usa DEFAULT_MATRIX "
              f"(que ya incluye {MODULO}). No hace falta migrar.")
        return 0

    actuales = list(doc.get("modules") or [])
    if MODULO in actuales:
        print(f"{ROL} ya tiene {MODULO} en la matriz viva. Nada que hacer.")
        return 0

    nuevos = actuales + [MODULO]
    print(f"{ROL}: {len(actuales)} → {len(nuevos)} módulos (agrega {MODULO}).")
    print(f"  módulos resultantes: {nuevos}")
    if args.dry_run:
        print("[dry-run] no se escribió nada.")
        return 0

    set_role_modules(ROL, nuevos, actor="script:grant_manager_titulos")
    print("✅ Matriz actualizada + cache de roles invalidado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
