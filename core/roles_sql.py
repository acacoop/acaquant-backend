"""core/roles_sql.py — lecturas de AUTH (roles/matriz) desde Postgres.

Lecturas PURAS. El orquestador con FALLBACK vive en core/roles.py (decide SQL vs Mongo y,
ante CUALQUIER error acá, cae a Mongo → prender AUTH_SQL nunca puede lockear). core/ solo
importa core/ (regla de capas): solo psycopg + core.postgres. MODULES se importa lazy adentro
de la función para no crear ciclo con core/roles.py.
"""
from __future__ import annotations

from psycopg.rows import dict_row

from core.postgres import get_pool


def _q(sql: str, params=None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def load_matrix_sql() -> dict[str, tuple[str, ...]]:
    """role → tuple(modules) desde role_matrix, filtrado a MODULES canónicos. {} si vacía
    (el caller cae a Mongo→DEFAULT_MATRIX)."""
    from core.roles import MODULES
    out: dict[str, list[str]] = {}
    for r in _q("SELECT role, module FROM role_matrix"):
        if r["module"] in MODULES:
            out.setdefault(r["role"], []).append(r["module"])
    return {k: tuple(v) for k, v in out.items()}


def set_role_modules_sql(role: str, modules: list[str]) -> None:
    """Reemplaza los módulos de un role en `role_matrix` (DELETE + INSERT). Espeja la
    escritura a Mongo (fuente de verdad) para que la LECTURA SQL quede consistente al
    instante — sin esto, con AUTH_SQL prendido el panel lee SQL stale y "no guarda"
    (la sync recién corre cada 20 min). Filtra a MODULES canónicos. Si falla, el caller
    lo ignora: Mongo ya persistió y el próximo sync_postgres alinea SQL."""
    from core.roles import MODULES
    mods = [m for m in modules if m in MODULES]
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM role_matrix WHERE role = %s", (role,))
        if mods:
            cur.executemany(
                "INSERT INTO role_matrix (role, module) VALUES (%s, %s)",
                [(role, m) for m in mods],
            )
        conn.commit()


def lookup_role_sql(email: str) -> str | None:
    """role de manager_users. None si no existe o enabled=False (NULL = habilitado, igual que
    Mongo: solo el False explícito bloquea)."""
    if not email or email == "anon":
        return None
    rows = _q("SELECT role, enabled FROM manager_users WHERE email = %s", (email,))
    if not rows:
        return None
    r = rows[0]
    if r["enabled"] is False:
        return None
    return str(r["role"]) if r["role"] else None
