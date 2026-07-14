"""db_obs.py — observabilidad de espacio/salud de la base (Manager OBSERVABILIDAD → BASE).

Read-only sobre los catálogos de Postgres: tamaño total, por schema, top tablas
por tamaño con bloat (dead tuples), último autovacuum y último dato. El límite de
disco del plan NO lo expone Postgres → se toma de env `DB_DISK_LIMIT_GB` (default
8 = Supabase Pro) para el % usado; editable si se escala el disco.
"""
from __future__ import annotations

import os

from api.cache import cached
from api.services._sql import _q

# Columnas de "última actualización" candidatas (orden de preferencia).
_COLS_FECHA = ("updated_at", "ingestado_en", "generado_at", "ts", "ts_cierre",
               "fecha", "created_at")

_LIMIT_BYTES = int(float(os.getenv("DB_DISK_LIMIT_GB", "8")) * 1024 ** 3)


def _cols(schema: str, tabla: str) -> set[str]:
    rows = _q(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s",
        (schema, tabla),
    )
    return {r["column_name"] for r in rows}


def _ultimo_dato(schema: str, tabla: str) -> str | None:
    """max() de la primera columna de fecha conocida — la frescura de la tabla."""
    col = next((c for c in _COLS_FECHA if c in _cols(schema, tabla)), None)
    if not col:
        return None
    try:
        # schema/tabla/col salen de pg_catalog (no input de usuario) → seguro.
        r = _q(f'SELECT max("{col}") AS m FROM "{schema}"."{tabla}"')
        m = r[0]["m"] if r else None
        return str(m)[:19] if m is not None else None
    except Exception:
        return None


@cached(ttl=120)
def db_observabilidad(top: int = 30) -> dict:
    """Foto de espacio/salud de la base. Cache 120s (las queries de tamaño +
    max() por tabla no son gratis; el dato no cambia rápido)."""
    total = int(_q("SELECT pg_database_size(current_database()) AS b")[0]["b"])

    schemas = _q("""
        SELECT n.nspname AS schema,
               sum(pg_total_relation_size(c.oid))::bigint AS bytes,
               count(*) AS tablas
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r'
          AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')
        GROUP BY n.nspname ORDER BY bytes DESC
    """)

    tablas = _q("""
        SELECT n.nspname AS schema, c.relname AS tabla,
               pg_total_relation_size(c.oid)::bigint AS total_b,
               pg_relation_size(c.oid)::bigint AS tabla_b,
               pg_indexes_size(c.oid)::bigint AS idx_b,
               s.n_live_tup, s.n_dead_tup, s.last_autovacuum
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
        WHERE c.relkind = 'r'
          AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')
        ORDER BY pg_total_relation_size(c.oid) DESC
        LIMIT %s
    """, (top,))

    out_tablas = []
    for t in tablas:
        live = t["n_live_tup"] or 0
        dead = t["n_dead_tup"] or 0
        lav = t["last_autovacuum"]
        out_tablas.append({
            "schema":        t["schema"],
            "tabla":         t["tabla"],
            "total_bytes":   t["total_b"],
            "tabla_bytes":   t["tabla_b"],
            "indices_bytes": t["idx_b"],
            "filas_vivas":   live,
            "filas_muertas": dead,
            "dead_pct":      round(dead / (live + dead) * 100, 1) if (live + dead) else 0,
            "ultimo_dato":   _ultimo_dato(t["schema"], t["tabla"]),
            "last_autovacuum": lav.isoformat() if lav else None,
        })

    return {
        "total_bytes": total,
        "limit_bytes": _LIMIT_BYTES,
        "usado_pct":   round(total / _LIMIT_BYTES * 100, 1) if _LIMIT_BYTES else None,
        "schemas":     [{"schema": s["schema"], "bytes": int(s["bytes"]),
                         "tablas": s["tablas"]} for s in schemas],
        "tablas":      out_tablas,
    }
