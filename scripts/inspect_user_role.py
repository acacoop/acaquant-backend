"""inspect_user_role.py — diagnosticar el RBAC efectivo de un email.

Mira en orden:
  1. Manager.Users  → ¿Qué role tiene grabado en la DB?
  2. Manager.RoleMatrix → ¿Qué módulos tiene ese role hoy?
  3. MANAGER_EMAILS env → ¿Está en la whitelist legacy de admin?
  4. CF_TRUSTED_SERVICE_TOKENS → ¿Es un service token con auto-admin?
  5. core.roles.get_user_role / get_user_modules → resultado efectivo,
     respetando todos los fallbacks que la API aplica.

Después imprime un veredicto claro: qué módulos VA a ver el user en
el frontend según el backend.

Si el rol que ves en la UI difiere del veredicto del script, el bug
está en el frontend (cache de /api/me, proxy.ts, etc.), no en el backend.

Uso:
    python -m scripts.inspect_user_role someone@example.com
"""
from __future__ import annotations

import json
import sys

from config import CF_TRUSTED_SERVICE_TOKENS, MANAGER_EMAILS
from core.mongo import get_mongo_client
from core.roles import (
    DEFAULT_ROLE,
    MODULES,
    get_matrix,
    get_user_modules,
    get_user_role,
    invalidate_cache,
)


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python -m scripts.inspect_user_role <email>", file=sys.stderr)
        sys.exit(2)

    email_raw = sys.argv[1].strip()
    email = email_raw.lower()
    print(f"== Diagnóstico RBAC para: {email!r}\n")

    # Limpio cache para que las lecturas reflejen Mongo en este momento.
    invalidate_cache()

    # 1. Manager.Users
    client = get_mongo_client()
    user_doc = client["Manager"]["Users"].find_one({"email": email}, {"_id": 0})
    print("[1] Manager.Users")
    if user_doc:
        print(json.dumps(user_doc, default=str, indent=2, ensure_ascii=False))
    else:
        print(f"  ❌ NO existe doc para {email!r}")
    print()

    # 2. Manager.RoleMatrix
    matrix = get_matrix()
    print("[2] Manager.RoleMatrix (vigente, post-invalidate_cache)")
    for role, mods in matrix.items():
        print(f"  {role}: {list(mods)}")
    print()

    # 3. MANAGER_EMAILS env (whitelist legacy)
    en_whitelist = email in MANAGER_EMAILS
    print(f"[3] MANAGER_EMAILS env → contiene {email!r}? {en_whitelist}")
    if MANAGER_EMAILS:
        print(f"    valores actuales: {sorted(MANAGER_EMAILS)}")
    print()

    # 4. CF_TRUSTED_SERVICE_TOKENS
    es_service = email.startswith("service:")
    cn = email.split(":", 1)[1] if es_service else None
    print(f"[4] CF_TRUSTED_SERVICE_TOKENS → ¿es service token? {es_service}")
    if es_service:
        print(f"    common_name extraído: {cn!r}")
        print(f"    en whitelist? {cn in CF_TRUSTED_SERVICE_TOKENS}")
    print()

    # 5. Resultado efectivo (lo que la API decide)
    role_efectivo = get_user_role(email)
    modulos_efectivos = list(get_user_modules(email))
    print("[5] Veredicto efectivo (lo que la API devuelve en /api/me)")
    print(f"    role     = {role_efectivo!r}")
    print(f"    modules  = {modulos_efectivos}")
    print(f"    is_admin = {role_efectivo == 'admin'}")
    print()

    # 6. Diff vs lo esperado
    print("[6] Veredicto humano")
    if role_efectivo == "admin":
        print(f"    → {email!r} VE TODO. Si esto es incorrecto:")
        if user_doc and user_doc.get("role") == "admin":
            print("      • doc en Users tiene role=admin. Cambialo a 'trader'/'sales' en /manager → USUARIOS.")
        elif en_whitelist:
            print("      • email está en MANAGER_EMAILS env (legacy). Sacalo del .env del Droplet.")
        else:
            print("      • investigá origen del role admin (auto_registered? service token?).")
    else:
        modulos_visibles = ", ".join(modulos_efectivos) or "(ninguno)"
        modulos_ocultos = [m for m in MODULES if m not in modulos_efectivos]
        print(f"    → {email!r} ve los módulos: {modulos_visibles}")
        if modulos_ocultos:
            print(f"      no ve: {', '.join(modulos_ocultos)}")

    if role_efectivo == DEFAULT_ROLE and not user_doc:
        print(f"      • cae al DEFAULT_ROLE='{DEFAULT_ROLE}' por no estar en Manager.Users.")


if __name__ == "__main__":
    main()
