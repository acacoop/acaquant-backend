"""Helper compartido de lectura SQL (canónico).

`_q` estaba copy-pasteado en ~20 módulos de `api/` y `_f` en varios más. Acá vive
la única implementación:

- `_q(sql, params)` → ejecuta la query contra el pool de Postgres
  (`core.postgres.get_pool`) y devuelve las filas como `list[dict]`
  (`row_factory=dict_row`). Acepta params posicionales (tuple) o nombrados (dict).
- `_f(v)` → cast a float preservando `None` (numeric/Decimal de Postgres → float).

Módulo PURO: solo psycopg + `core.postgres`, sin FastAPI (regla de capas de
`api/services/`).
"""

from __future__ import annotations

from psycopg.rows import dict_row

from core.postgres import get_pool


def _q(sql: str, params: dict | tuple | None = None) -> list[dict]:
    """Ejecuta `sql` y devuelve todas las filas como dicts."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def _f(v) -> float | None:
    """numeric (Decimal) → float, preservando None."""
    return float(v) if v is not None else None
