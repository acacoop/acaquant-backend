"""core/grupos_sql.py — lectura del scope de cuentas (grupos) desde Postgres.

Lectura PURA. El orquestador con FALLBACK vive en core/grupos.py. core/ solo importa core/.
"""
from __future__ import annotations

from psycopg.rows import dict_row

from core.postgres import get_pool


def _q(sql: str, params=None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def cuentas_de_grupos_sql(email: str) -> set[str] | None:
    """None si el email NO está en ningún grupo (→ ve todo); sino la unión de id_cuentas
    de sus grupos (puede ser set vacío = no ve nada)."""
    rows = _q("SELECT id_cuentas FROM grupos WHERE %s = ANY(emails)", (email,))
    if not rows:
        return None
    cuentas: set[str] = set()
    for r in rows:
        for c in (r["id_cuentas"] or []):
            cuentas.add(str(c))
    return cuentas
