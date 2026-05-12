"""Quita el módulo 'renta-variable' de los roles 'trader' y 'sales' en Manager.RoleMatrix.

Después de este script, solo 'admin' ve la vista /renta-variable.

Por qué este script existe: cambiar `DEFAULT_MATRIX` en `core/roles.py` no se
propaga a `Manager.RoleMatrix` cuando esa colección ya está poblada (caso
producción). El admin tendría que editar la matriz desde /manager → ROLES Y
PERMISOS, o correr este script.

Idempotente: si el módulo no está en el role, no hace nada.

Uso:
    python -m scripts.restrict_renta_variable_admin
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.mongo import get_mongo_client

MODULE = "renta-variable"
ROLES_TO_REMOVE_FROM = ("trader", "sales")


def run() -> None:
    db = get_mongo_client()
    col = db["Manager"]["RoleMatrix"]
    ts = datetime.now(UTC)

    print(f"Quitando módulo '{MODULE}' de roles: {ROLES_TO_REMOVE_FROM}\n")
    changed = 0

    for role in ROLES_TO_REMOVE_FROM:
        doc = col.find_one({"role": role})
        if not doc:
            print(f"   ⚠ role {role!r} NO existe en Manager.RoleMatrix — salteo")
            continue
        modules = list(doc.get("modules") or [])
        if MODULE not in modules:
            print(f"   ✓ {role:<7}: '{MODULE}' no estaba (nada que hacer)")
            continue
        new_modules = [m for m in modules if m != MODULE]
        col.update_one(
            {"role": role},
            {"$set": {
                "modules":    new_modules,
                "updated_at": ts,
                "updated_by": "system:restrict_renta_variable_admin",
            }},
        )
        db["Manager"]["RoleAudit"].insert_one({
            "ts":     ts,
            "actor":  "system",
            "action": "remove_module",
            "target": role,
            "before": {"modules": modules},
            "after":  {"modules": new_modules},
            "note":   f"quitado {MODULE} via restrict script",
        })
        print(f"   ✓ {role:<7}: quitado '{MODULE}'  ({len(new_modules)} módulos restantes)")
        changed += 1

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
