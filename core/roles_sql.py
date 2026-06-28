"""core/roles_sql.py — lecturas + escrituras de AUTH (roles/matriz/usuarios) desde Postgres.

Funciones PURAS sobre `manager.manager_users` / `manager.role_matrix`. El orquestador con
FALLBACK (lecturas) y el DUAL-WRITE best-effort (escrituras) viven en core/roles.py: Mongo
sigue siendo la fuente de verdad y el fallback de lectura; estos writers mantienen el espejo
SQL FRESCO al instante (sin esperar los 20 min del sync_postgres) para que con AUTH_SQL=1 el
panel lea SQL consistente. core/ solo importa core/ (regla de capas): solo psycopg +
core.postgres. MODULES se importa lazy adentro de cada función para no ciclar con core/roles.py.

Las escrituras SQL son best-effort: el caller (core/roles.py) las envuelve en try/except y NUNCA
deja que un error SQL tumbe la mutación real (que ya persistió en Mongo). Semántica preservada
1:1 con Mongo — ver cada docstring.
"""
from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row

from core.postgres import get_pool


def _q(sql: str, params=None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def _exec(sql: str, params=None) -> int:
    """Ejecuta un statement de escritura y commitea. Devuelve rowcount."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or ())
        n = cur.rowcount or 0
        conn.commit()
        return n


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


# ─────────────────────────────────────────────────────────────
# Escrituras (espejo SQL de las mutaciones de core/roles.py)
# ─────────────────────────────────────────────────────────────

def upsert_user_sql(email: str, role: str, enabled: bool, notes: str, now: datetime) -> None:
    """Espejo SQL de roles.upsert_user. Replica el $set/$setOnInsert de Mongo:
      - $set      → role, enabled, notes, updated_at  (se pisan en cada upsert)
      - $setOnInsert → created_at  (SOLO al crear; NO se pisa en updates)
    `created_at` y `auto_registered` quedan FUERA del DO UPDATE SET → en un upsert sobre
    una fila existente NO se tocan (idéntico a Mongo, que no los lista en $set). En el INSERT
    inicial created_at = now y auto_registered queda NULL (= "creado a mano", no auto)."""
    _exec(
        """
        INSERT INTO manager_users (email, role, enabled, notes, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (email) DO UPDATE SET
            role       = EXCLUDED.role,
            enabled    = EXCLUDED.enabled,
            notes      = EXCLUDED.notes,
            updated_at = EXCLUDED.updated_at
        """,
        (email, role, bool(enabled), notes or "", now, now),
    )


def delete_user_sql(email: str) -> bool:
    """Espejo SQL de roles.delete_user. True si borró una fila."""
    return _exec("DELETE FROM manager_users WHERE email = %s", (email,)) > 0


def touch_last_seen_sql(email: str, now: datetime, threshold: datetime) -> None:
    """Espejo SQL de roles._touch_last_seen. Throttle replicado con el WHERE: solo escribe si
    `last_seen_at` es NULL (legacy / nunca visto) o más viejo que el umbral — idéntico al
    filtro `$or:[{$lt: threshold}, {$exists: False}]` de Mongo."""
    _exec(
        """
        UPDATE manager_users SET last_seen_at = %s
        WHERE email = %s AND (last_seen_at IS NULL OR last_seen_at < %s)
        """,
        (now, email, threshold),
    )


def auto_register_sql(email: str, role: str, notes: str, now: datetime) -> None:
    """Espejo SQL de roles._auto_register. Replica el upsert Mongo:
      - $set      → email, updated_at
      - $setOnInsert → role, enabled=True, auto_registered=True, notes, created_at
    En el INSERT setea todo; en CONFLICT (fila ya existe) SOLO toca updated_at — NO pisa
    role/enabled/auto_registered/notes/created_at (idéntico a $setOnInsert, que no aplica
    si el doc ya existía)."""
    _exec(
        """
        INSERT INTO manager_users
            (email, role, enabled, auto_registered, notes, created_at, updated_at)
        VALUES (%s, %s, TRUE, TRUE, %s, %s, %s)
        ON CONFLICT (email) DO UPDATE SET updated_at = EXCLUDED.updated_at
        """,
        (email, role, notes or "", now, now),
    )
