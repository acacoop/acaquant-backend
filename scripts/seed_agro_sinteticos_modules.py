"""One-shot — agrega los módulos nuevos `agro` y `sinteticos` a Manager.RoleMatrix.

Contexto: al sacar Agro y Sintéticos del módulo `derivados` y promoverlos a
top-level, los usuarios necesitan tenerlos en su matriz de permisos para
que aparezcan en el sidebar. `DEFAULT_MATRIX` ya los incluye para los 3
roles (admin/trader/sales), pero la matriz de prod (Manager.RoleMatrix)
está poblada y no se auto-actualiza al cambiar el código (ver el comentario
en core/roles.py).

Este script:
  1. Lee los 3 roles (admin, trader, sales) de Manager.RoleMatrix.
  2. Si a un role le falta `agro` o `sinteticos`, los agrega.
  3. Loguea cada cambio en Manager.RoleAudit con actor = "seed:<script>".
  4. Invalida el cache in-memory (al próximo request todos los roles ven
     los módulos nuevos).

Idempotente: correr múltiples veces no rompe nada.

Uso:
    python -m scripts.seed_agro_sinteticos_modules
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.mongo import get_mongo_client
from core.roles import invalidate_cache

NEW_MODULES = ("agro", "sinteticos")
ROLES = ("admin", "trader", "sales")
ACTOR = "seed:seed_agro_sinteticos_modules"


def main() -> int:
    db = get_mongo_client()["Manager"]
    matrix = db["RoleMatrix"]
    audit = db["RoleAudit"]
    now = datetime.now(UTC)

    cambios = 0
    print(f"Buscando módulos {NEW_MODULES} en Manager.RoleMatrix…")
    for role in ROLES:
        doc = matrix.find_one({"role": role}) or {}
        actuales: list[str] = list(doc.get("modules") or [])
        faltantes = [m for m in NEW_MODULES if m not in actuales]
        if not faltantes:
            print(f"  [{role}] ya tiene {NEW_MODULES} — nada que hacer.")
            continue

        nuevos = actuales + faltantes
        matrix.update_one(
            {"role": role},
            {
                "$set": {
                    "role":       role,
                    "modules":    nuevos,
                    "updated_by": ACTOR,
                    "updated_at": now,
                },
            },
            upsert=True,
        )
        audit.insert_one({
            "role":       role,
            "prev":       actuales,
            "new":        nuevos,
            "actor":      ACTOR,
            "updated_at": now,
        })
        cambios += 1
        print(f"  [{role}] agregado: {faltantes}  →  total {len(nuevos)} módulos.")

    invalidate_cache()
    print(f"\nListo. {cambios} role(s) actualizado(s). Cache invalidado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
