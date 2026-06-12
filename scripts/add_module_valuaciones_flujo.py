"""scripts/add_module_valuaciones_flujo.py — habilita el módulo 'valuaciones-flujo'.

La RoleMatrix viva (Manager.RoleMatrix) PISA el DEFAULT_MATRIX de core/roles.py:
agregar el módulo al código NO basta, hay que sumarlo a la matriz en Mongo para
admin + asistente_comercial. Idempotente ($addToSet).

Alternativa sin script: Manager → ROLES Y PERMISOS, tildar 'valuaciones-flujo'
en admin y asistente_comercial (la UI ya lo lista tras el restart de la API).

Si AUTH_SQL=1, el cambio se propaga a la tabla SQL role_matrix en la próxima
corrida de jobs.sync_postgres (cron c/20 min) — o corrés el sync a mano.

Uso:
    python -m scripts.add_module_valuaciones_flujo --dry-run
    python -m scripts.add_module_valuaciones_flujo
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

_MOD = "valuaciones-flujo"
_ROLES = ("admin", "asistente_comercial")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="no escribe, solo muestra")
    args = ap.parse_args()

    col = get_mongo_client()["Manager"]["RoleMatrix"]
    for role in _ROLES:
        doc = col.find_one({"role": role}, {"_id": 0, "modules": 1})
        tiene = bool(doc) and _MOD in (doc.get("modules") or [])
        estado = "ya lo tiene" if tiene else ("agregar" if doc else "crear doc + agregar")
        print(f"  {role:<22} {estado}")
        if args.dry_run or tiene:
            continue
        col.update_one({"role": role}, {"$addToSet": {"modules": _MOD}}, upsert=True)

    if args.dry_run:
        print("(dry-run: no se escribió nada)")
    else:
        print(f"\nOK: '{_MOD}' habilitado para {', '.join(_ROLES)}.")
        print("Reiniciá api.service (o esperá 60s, TTL del cache de roles).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
