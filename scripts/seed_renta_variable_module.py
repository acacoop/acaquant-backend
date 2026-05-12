"""Agrega el módulo 'renta-variable' a Manager.RoleMatrix (admin/trader/sales).

Por qué este script existe: agregar un módulo a `core/roles.py::MODULES` y
`DEFAULT_MATRIX` NO se propaga automáticamente a la colección Mongo
`Manager.RoleMatrix` cuando esa colección ya está poblada (caso producción).
El admin tendría que editar la matriz desde /manager → ROLES Y PERMISOS, o
correr este script.

Idempotente: si el módulo ya está en algún role, no lo duplica.

Uso:
    python -m scripts.seed_renta_variable_module
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.mongo import get_mongo_client

NEW_MODULE = "renta-variable"
ROLES_TO_GET_MODULE = ("admin", "trader", "sales")


def run() -> None:
    db = get_mongo_client()
    col = db["Manager"]["RoleMatrix"]
    ts = datetime.now(UTC)

    print(f"Agregando módulo '{NEW_MODULE}' a roles: {ROLES_TO_GET_MODULE}\n")
    changed = 0

    for role in ROLES_TO_GET_MODULE:
        doc = col.find_one({"role": role})
        if not doc:
            print(f"   ⚠ role {role!r} NO existe en Manager.RoleMatrix — salteo")
            continue
        modules = list(doc.get("modules") or [])
        if NEW_MODULE in modules:
            print(f"   ✓ {role:<7}: '{NEW_MODULE}' ya estaba")
            continue
        modules.append(NEW_MODULE)
        col.update_one(
            {"role": role},
            {"$set": {
                "modules":    modules,
                "updated_at": ts,
                "updated_by": "system:seed_renta_variable_module",
            }},
        )
        # Audit log
        db["Manager"]["RoleAudit"].insert_one({
            "ts":     ts,
            "actor":  "system",
            "action": "add_module",
            "target": role,
            "before": {"modules": doc.get("modules")},
            "after":  {"modules": modules},
            "note":   f"agregado {NEW_MODULE} via seed script",
        })
        print(f"   ✓ {role:<7}: agregado '{NEW_MODULE}'  ({len(modules)} módulos en total)")
        changed += 1

    # Invalidar cache in-process (importante si el script corre mientras el
    # api.service está vivo — sino el role-check sigue devolviendo el
    # caché viejo durante hasta TTL=60s).
    try:
        from core.roles import invalidate_cache
        invalidate_cache()
        print("\n→ core.roles.invalidate_cache() llamado en este proceso.")
        print("  El api.service running tiene su propio cache TTL=60s — esperá")
        print("  hasta 1 minuto o reiniciá api.service para refresh inmediato.")
    except Exception as e:
        print(f"\n⚠ no pude invalidar cache: {e}")

    print(f"\nListos: {changed} role(s) actualizados.")


if __name__ == "__main__":
    run()
