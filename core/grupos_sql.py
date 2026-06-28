"""core/grupos_sql.py — lectura + escritura del scope de cuentas (grupos) desde Postgres.

Funciones PURAS sobre `manager.grupos`. El orquestador con FALLBACK (lectura) y el DUAL-WRITE
best-effort (escritura) viven en core/grupos.py: Mongo sigue siendo la fuente de verdad y el
fallback; estos writers mantienen el espejo SQL FRESCO al instante (sin esperar los 20 min del
sync_postgres). core/ solo importa core/.

PK `id`: en modo dual-write el caller pasa el MISMO str(ObjectId) que generó el insert Mongo
→ ambas filas (Mongo/SQL) comparten clave y el sync no duplica. (El uuid SQL-native sólo aplica
cuando se corte Mongo del todo.) `emails`/`id_cuentas` se guardan como arrays text[] (psycopg
adapta la lista Python directo). OJO: la tabla SQL NO tiene columna `updated_por` (Mongo sí) →
ese campo se pierde en el espejo, es metadata no consumida por ninguna lectura.
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
