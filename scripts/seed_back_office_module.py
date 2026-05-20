"""One-shot — agrega el módulo `back-office` a Manager.RoleMatrix en Atlas.

Mismo patrón que `seed_agro_sinteticos_modules.py`: `DEFAULT_MATRIX` ya lo
incluye en `core/roles.py`, pero la matriz de prod (`Manager.RoleMatrix`)
está poblada y no se auto-actualiza al cambiar el código.

Por default abrimos `back-office` a admin/trader/sales (queda a tu criterio
restringirlo desde /manager si querés). Idempotente.

Uso:
    python -m scripts.seed_back_office_module
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.mongo import get_mongo_client
from core.roles import invalidate_cache

NEW_MODULE = "back-office"
ROLES = ("admin", "trader", "sales")
ACTOR = "seed:seed_back_office_module"


def main() -> int:
    db = get_mongo_client()["Manager"]
    matrix = db["RoleMatrix"]
    audit = db["RoleAudit"]
    now = datetime.now(UTC)

    cambios = 0
    print(f"Buscando módulo {NEW_MODULE!r} en Manager.RoleMatrix…")
    for role in ROLES:
        doc = matrix.find_one({"role": role}) or {}
        actuales: list[str] = list(doc.get("modules") or [])
        if NEW_MODULE in actuales:
            print(f"  [{role}] ya lo tiene — skip.")
            continue

        nuevos = [*actuales, NEW_MODULE]
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
        print(f"  [{role}] agregado → total {len(nuevos)} módulos.")

    invalidate_cache()
    print(f"\nListo. {cambios} role(s) actualizado(s). Cache invalidado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
