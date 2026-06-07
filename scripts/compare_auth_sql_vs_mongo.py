"""scripts/compare_auth_sql_vs_mongo.py — valida AUTH SQL == Mongo antes de prender AUTH_SQL.

100% LECTURA. Compara, SIN tocar el read-path productivo: la matriz de roles, el role de
CADA usuario, y el scope de cuentas de cada usuario, leídos de SQL vs Mongo. Gate del flag
AUTH_SQL. Ver docs/MIGRACION_MONGO_SUPABASE.md.

    python -m scripts.compare_auth_sql_vs_mongo
"""
from __future__ import annotations

import sys

from core import grupos_sql, roles, roles_sql
from core.mongo import get_mongo_client


def _mongo_grupos(email: str) -> set[str] | None:
    cuentas: set[str] = set()
    en = False
    for g in get_mongo_client()["Manager"]["Grupos"].find(
        {"emails": email}, {"_id": 0, "id_cuentas": 1}
    ):
        en = True
        for c in g.get("id_cuentas") or []:
            cuentas.add(str(c))
    return cuentas if en else None


def main() -> int:
    fails: list[str] = []

    # 1. Matriz role → modules
    m_sql = roles_sql.load_matrix_sql()
    m_mongo = roles._load_matrix_from_db()
    roles_set = set(m_sql) | set(m_mongo)
    for r in sorted(roles_set):
        a = set(m_mongo.get(r, ()))
        b = set(m_sql.get(r, ()))
        if a != b:
            fails.append(f"matrix[{r}]: mongo={sorted(a)} sql={sorted(b)}")
    print(f"matriz: {len(roles_set)} roles comparados")

    # 2. role por usuario + 3. scope por usuario
    emails = sorted({u.get("email", "").lower() for u in roles.list_users() if u.get("email")})
    print(f"usuarios: {len(emails)}")
    for em in emails:
        rs, rm = roles_sql.lookup_role_sql(em), roles._lookup_role_db(em)
        if rs != rm:
            fails.append(f"role[{em}]: mongo={rm!r} sql={rs!r}")
        gs, gm = grupos_sql.cuentas_de_grupos_sql(em), _mongo_grupos(em)
        if gs != gm:
            fails.append(f"scope[{em}]: mongo={gm} sql={gs}")

    for f in fails:
        print(f"  [FAIL] {f}")
    if fails:
        print(f"\n❌ {len(fails)} diferencias — NO prender AUTH_SQL.")
        return 1
    print(f"\n✅ AUTH SQL == Mongo ({len(emails)} usuarios, {len(roles_set)} roles). "
          f"Seguro para AUTH_SQL=1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
