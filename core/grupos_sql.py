"""core/grupos_sql.py — lectura + escritura del scope de cuentas (grupos) desde Postgres.

Funciones PURAS sobre `manager.grupos`, que es la fuente de verdad. El orquestador
vive en core/grupos.py. core/ solo importa core/.

PK `id`: uuid generado en el insert. `emails`/`id_cuentas` se guardan como arrays
text[] (psycopg adapta la lista Python directo). OJO: la tabla NO tiene columna
`updated_por` — ese campo no se persiste, es metadata que ninguna lectura consume.
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


def _row_to_grupo(r: dict) -> dict:
    """Fila SQL → dict con el shape que espera el panel (la PK `id` se expone como `_id`,
    igual que el path Mongo que devolvía str(ObjectId))."""
    return {
        "_id":        r["id"],
        "nombre":     r["nombre"],
        "emails":     list(r["emails"] or []),
        "id_cuentas": list(r["id_cuentas"] or []),
        "creado_por": r["creado_por"],
        "creado_at":  r["creado_at"],
        "updated_at": r["updated_at"],
    }


def listar_grupos_sql() -> list[dict]:
    """Todos los grupos ordenados por nombre (mismo orden/shape que el path Mongo). Lo
    consume el panel /api/manager/grupos."""
    rows = _q(
        "SELECT id, nombre, emails, id_cuentas, creado_por, creado_at, updated_at "
        "FROM grupos ORDER BY nombre"
    )
    return [_row_to_grupo(r) for r in rows]


def get_grupo_sql(grupo_id: str) -> dict | None:
    """Un grupo por id (mismo shape que listar_grupos_sql). None si no existe. Lo usa
    actualizar_grupo de core/grupos.py para devolver el doc actualizado."""
    rows = _q(
        "SELECT id, nombre, emails, id_cuentas, creado_por, creado_at, updated_at "
        "FROM grupos WHERE id = %s",
        (grupo_id,),
    )
    return _row_to_grupo(rows[0]) if rows else None


# ─────────────────────────────────────────────────────────────
# Escrituras (espejo SQL de las mutaciones de core/grupos.py)
# ─────────────────────────────────────────────────────────────

def crear_grupo_sql(grupo_id: str, nombre: str, emails: list[str], id_cuentas: list[str],
                    creado_por: str, creado_at: datetime, updated_at: datetime) -> None:
    """Espejo SQL de grupos.crear_grupo. `grupo_id` = str(ObjectId) del insert Mongo (misma
    PK en ambas bases). ON CONFLICT DO NOTHING por idempotencia (re-correr no rompe)."""
    _exec(
        """
        INSERT INTO grupos (id, nombre, emails, id_cuentas, creado_por, creado_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (grupo_id, nombre, list(emails), list(id_cuentas), creado_por, creado_at, updated_at),
    )


def actualizar_grupo_sql(grupo_id: str, nombre: str, emails: list[str],
                         id_cuentas: list[str], updated_at: datetime) -> bool:
    """Espejo SQL de grupos.actualizar_grupo. Pisa nombre/emails/id_cuentas/updated_at; NO toca
    creado_por/creado_at (idéntico al $set Mongo, que tampoco los lista). True si matcheó fila."""
    return _exec(
        """
        UPDATE grupos SET nombre = %s, emails = %s, id_cuentas = %s, updated_at = %s
        WHERE id = %s
        """,
        (nombre, list(emails), list(id_cuentas), updated_at, grupo_id),
    ) > 0


def eliminar_grupo_sql(grupo_id: str) -> bool:
    """Espejo SQL de grupos.eliminar_grupo. True si borró una fila."""
    return _exec("DELETE FROM grupos WHERE id = %s", (grupo_id,)) > 0
