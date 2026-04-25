"""Seed de Manager.RoleMatrix + Manager.Users (bootstrap del sistema de roles).

Idempotente: se puede correr múltiples veces sin duplicar ni romper state.

Qué hace:
  1. Crea índices en Manager.Users y Manager.RoleAudit si no existen.
  2. Escribe la matriz default (3 roles: admin/trader/sales) en
     Manager.RoleMatrix si la colección está vacía o si se pasa --force.
     Si ya hay docs, los respeta (no sobrescribe configuración manual).
  3. Seedea todos los emails de config.MANAGER_EMAILS como role=admin
     en Manager.Users (upsert). Si el doc ya existía, lo deja como está.

Uso:
    python -m scripts.seed_roles              # idempotente
    python -m scripts.seed_roles --force      # reescribe la matriz
    python -m scripts.seed_roles --dry        # preview sin tocar

Cloudflare sigue siendo el gate de quién puede ENTRAR. Este seed solo
asegura que los admins actuales (MANAGER_EMAILS) tengan role=admin en la
DB — el resto de emails que pasen CF quedan sin doc y caen al fallback
legacy (MANAGER_EMAILS → admin) o a DEFAULT_ROLE=sales.
"""
from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime

from config import MANAGER_EMAILS
from core.mongo import get_mongo_client
from core.roles import DEFAULT_MATRIX, MODULES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("seed_roles")


def seed_matrix(force: bool, dry: bool) -> int:
    """Seedea DEFAULT_MATRIX en Manager.RoleMatrix. Idempotente."""
    col = get_mongo_client()["Manager"]["RoleMatrix"]
    existing = list(col.find({}, {"_id": 0, "role": 1}))

    if existing and not force:
        logger.info(
            "RoleMatrix ya tiene %d roles (%s); respetando (usá --force para reescribir)",
            len(existing), [r["role"] for r in existing],
        )
        return 0

    if dry:
        logger.info("[dry] reescribiría RoleMatrix con %d roles", len(DEFAULT_MATRIX))
        return 0

    now = datetime.now(UTC)
    n = 0
    for role, mods in DEFAULT_MATRIX.items():
        col.update_one(
            {"role": role},
            {"$set": {
                "role": role,
                "modules": list(mods),
                "updated_by": "seed_roles",
                "updated_at": now,
            }},
            upsert=True,
        )
        n += 1
        logger.info("  %s: %s", role, ", ".join(mods))
    return n


def seed_admins(dry: bool) -> int:
    """Seedea los emails de MANAGER_EMAILS como role=admin. Idempotente."""
    if not MANAGER_EMAILS:
        logger.warning("MANAGER_EMAILS vacío en config — nada que seedear")
        return 0

    col = get_mongo_client()["Manager"]["Users"]
    now = datetime.now(UTC)
    n = 0
    for email in MANAGER_EMAILS:
        email_norm = email.lower().strip()
        if not email_norm:
            continue
        existing = col.find_one({"email": email_norm}, {"_id": 0, "role": 1})
        if existing:
            logger.info("  %s: ya existe (role=%s)", email_norm, existing.get("role"))
            continue

        if dry:
            logger.info("[dry] crearía %s como admin", email_norm)
            continue

        col.update_one(
            {"email": email_norm},
            {
                "$set": {
                    "email": email_norm,
                    "role": "admin",
                    "enabled": True,
                    "notes": "seed inicial desde MANAGER_EMAILS env",
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        n += 1
        logger.info("  %s: creado como admin", email_norm)
    return n


def ensure_indexes(dry: bool) -> None:
    if dry:
        logger.info("[dry] crearía índices en Manager.Users y Manager.RoleAudit")
        return
    client = get_mongo_client()
    client["Manager"]["Users"].create_index("email", unique=True)
    client["Manager"]["RoleMatrix"].create_index("role", unique=True)
    client["Manager"]["RoleAudit"].create_index([("ts", -1)])
    logger.info("índices OK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="Reescribe RoleMatrix aunque ya tenga docs")
    parser.add_argument("--dry", action="store_true",
                        help="Preview sin tocar Mongo")
    args = parser.parse_args()

    logger.info("=== seed_roles (force=%s, dry=%s) ===", args.force, args.dry)
    logger.info("Módulos canónicos: %s", ", ".join(MODULES))

    ensure_indexes(args.dry)
    n_matrix = seed_matrix(args.force, args.dry)
    n_users = seed_admins(args.dry)

    logger.info("Listo: %d roles en matriz, %d admins seedeados", n_matrix, n_users)


if __name__ == "__main__":
    main()
