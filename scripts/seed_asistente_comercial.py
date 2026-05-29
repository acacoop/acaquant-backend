"""seed_asistente_comercial.py — alta del rol `asistente_comercial` + sub-módulos de Manager.

Asegura en `Manager.RoleMatrix`:
  - Sub-módulos nuevos (`manager_comercial`, `manager_clientes`, `manager_clientes_bulk`)
    sumados al rol `admin` (sin pisar lo que ya tiene).
  - Rol `asistente_comercial` con la lista canónica de `core.roles.DEFAULT_MATRIX`.

Idempotente: re-correrlo no introduce drift. Cada cambio queda en `Manager.RoleAudit`
con `actor="scripts.seed_asistente_comercial"`.

El cache RBAC del proceso `api.service` tiene TTL 60s → la matriz nueva toma efecto
sin restart en a lo sumo 1 minuto. Si querés efecto inmediato, reiniciá api.service.

Uso:
    python -m scripts.seed_asistente_comercial --dry-run     # solo print, no escribe
    python -m scripts.seed_asistente_comercial --apply       # aplica los cambios
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

from core.mongo import get_mongo_client
from core.roles import DEFAULT_MATRIX, MODULES

ACTOR = "scripts.seed_asistente_comercial"

# Sub-módulos que tienen que estar en el rol `admin` (umbrella sigue siendo `manager`).
ADMIN_EXTRA_MODULES = ("manager_comercial", "manager_clientes", "manager_clientes_bulk")

# Lista canónica para el rol nuevo. Si querés cambiar el alcance, editá
# DEFAULT_MATRIX["asistente_comercial"] en core/roles.py y re-corré este script.
TARGET_ROLE = "asistente_comercial"
TARGET_MODULES = tuple(DEFAULT_MATRIX[TARGET_ROLE])


def _now() -> datetime:
    return datetime.now(UTC)


def _diff(before: list[str] | None, after: list[str]) -> tuple[list[str], list[str]]:
    """Devuelve (added, removed) comparando before vs after. None se trata como []."""
    b = set(before or [])
    a = set(after)
    return sorted(a - b), sorted(b - a)


def _ensure_role(col_matrix, col_audit, role: str, modules: list[str], apply: bool) -> bool:
    """Upsert del rol en RoleMatrix. Devuelve True si hubo cambios reales (added/removed)."""
    # Validar contra MODULES canónicos (evita persistir strings zombies).
    modules_valid = [m for m in modules if m in MODULES]
    if len(modules_valid) != len(modules):
        invalid = sorted(set(modules) - set(modules_valid))
        print(f"  ⚠ módulos no en MODULES, se ignoran: {invalid}")

    doc = col_matrix.find_one({"role": role}, {"_id": 0, "modules": 1})
    before_modules = doc.get("modules") if doc else None
    added, removed = _diff(before_modules, modules_valid)

    if not added and not removed and doc is not None:
        print(f"  • {role}: sin cambios ({len(modules_valid)} módulos)")
        return False

    print(f"  • {role}: +{added or '∅'}  -{removed or '∅'}  → {len(modules_valid)} módulos totales")
    if not apply:
        return True

    now = _now()
    col_matrix.update_one(
        {"role": role},
        {"$set": {
            "role": role,
            "modules": modules_valid,
            "updated_by": ACTOR,
            "updated_at": now,
        }},
        upsert=True,
    )
    after_doc = col_matrix.find_one({"role": role}, {"_id": 0})
    col_audit.insert_one({
        "ts":     now,
        "actor":  ACTOR,
        "action": "set_role_modules",
        "target": role,
        "before": {"role": role, "modules": before_modules} if before_modules is not None else None,
        "after":  after_doc,
    })
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--dry-run", action="store_true", help="solo print, no escribe")
    grupo.add_argument("--apply",   action="store_true", help="aplica los cambios a Mongo")
    args = parser.parse_args()
    apply = args.apply

    db = get_mongo_client()["Manager"]
    col_matrix = db["RoleMatrix"]
    col_audit  = db["RoleAudit"]

    print(f"[{'APPLY' if apply else 'DRY-RUN'}] seed asistente_comercial")
    print()

    # 1) admin: agregar los sub-módulos nuevos a lo que ya tenga.
    admin_doc = col_matrix.find_one({"role": "admin"}, {"_id": 0, "modules": 1})
    if admin_doc is None:
        # Bootstrap: admin no está en RoleMatrix todavía → sembrar con MODULES completo.
        admin_target = list(MODULES)
        print("admin no estaba en RoleMatrix — sembrando con MODULES completo:")
    else:
        admin_current = list(admin_doc.get("modules") or [])
        # Sumar los nuevos sin duplicar, preservando el orden de lo existente.
        admin_target = admin_current + [m for m in ADMIN_EXTRA_MODULES if m not in admin_current]
        print("admin:")
    _ensure_role(col_matrix, col_audit, "admin", admin_target, apply)

    print()
    print(f"{TARGET_ROLE}:")
    _ensure_role(col_matrix, col_audit, TARGET_ROLE, list(TARGET_MODULES), apply)

    print()
    if apply:
        print("✓ aplicado. Cache TTL 60s — efecto inmediato sin restart, o `systemctl "
              "restart api.service` para forzarlo ahora.")
    else:
        print("DRY-RUN. Re-correr con --apply para escribir.")


if __name__ == "__main__":
    main()
