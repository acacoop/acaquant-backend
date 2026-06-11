"""core/pg_mirror.py — dual-write best-effort Mongo→Postgres (Fase 2, capa MERCADO).

Los motores/jobs llaman a estas funciones DESPUÉS de su write a Mongo. Con el
flag apagado son no-op instantáneo (cero cambio de comportamiento). NUNCA
levantan: cualquier error se loguea y el caller sigue — Mongo es la base
operativa, Postgres es espejo descartable (ver docs/SQL.md).

Flags (env, default OFF):
    SNAPSHOT_SQL=1       → snapshots live de motores (tabla market_snapshot)
    MERCADO_SQL_WRITE=1  → jobs batch de mercado (series_macro, rem,
                           snapshots_cierre_hist, canje_cierre)

Semántica: UPSERT por PK de SOLO las columnas presentes en cada fila (los rows
se agrupan por set de claves) → replica el $set parcial de Mongo: un escritor
nunca pisa columnas que no le pertenecen.
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger("pg_mirror")

_CHUNK = 5000
_last_flush: dict[str, float] = {}


def snapshots_live_on() -> bool:
    return os.getenv("SNAPSHOT_SQL") == "1"


def jobs_on() -> bool:
    return os.getenv("MERCADO_SQL_WRITE") == "1"


def mirror_snapshot(table: str, key_cols: list[str], rows: list[dict],
                    min_interval: float = 0.0) -> int:
    """Espejo live desde un motor (flag SNAPSHOT_SQL). `min_interval` > 0
    descarta flushes más frecuentes que eso — usarlo SOLO si el caller
    re-escribe el estado COMPLETO en cada iteración (valores.py); un escritor
    de deltas (curvas.py) debe pasar 0 o perdería updates hasta el próximo
    cambio de precio."""
    if not snapshots_live_on() or not rows:
        return 0
    if min_interval > 0:
        now = time.monotonic()
        if now - _last_flush.get(table, 0.0) < min_interval:
            return 0
        _last_flush[table] = now
    return _mirror(table, key_cols, rows)


def mirror_job(table: str, key_cols: list[str], rows: list[dict]) -> int:
    """Espejo batch desde un job de mercado (flag MERCADO_SQL_WRITE)."""
    if not jobs_on() or not rows:
        return 0
    return _mirror(table, key_cols, rows)


def _mirror(table: str, key_cols: list[str], rows: list[dict]) -> int:
    try:
        import json
        from functools import partial

        from psycopg.types.json import Jsonb

        from core.postgres import get_pool

        dumps = partial(json.dumps, default=str)  # datetimes dentro de jsonb → str

        def _adapt(v):
            return Jsonb(v, dumps=dumps) if isinstance(v, dict | list) else v

        # Agrupar por set de columnas → cada grupo upsertea SOLO sus columnas
        # (semántica $set parcial; filas con campos distintos no se contaminan).
        grupos: dict[tuple, list[tuple]] = {}
        for r in rows:
            cols = tuple(r.keys())
            grupos.setdefault(cols, []).append(tuple(_adapt(r[c]) for c in cols))

        n = 0
        with get_pool().connection() as conn, conn.cursor() as cur:
            for cols, vals in grupos.items():
                sets = ",".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in key_cols)
                on_conflict = f"DO UPDATE SET {sets}" if sets else "DO NOTHING"
                sql = (f"INSERT INTO {table} ({','.join(cols)}) "
                       f"VALUES ({','.join(['%s'] * len(cols))}) "
                       f"ON CONFLICT ({','.join(key_cols)}) {on_conflict}")
                for i in range(0, len(vals), _CHUNK):
                    cur.executemany(sql, vals[i:i + _CHUNK])
                n += len(vals)
        return n
    except Exception as e:
        logger.error("pg_mirror %s: %s", table, str(e).splitlines()[0][:200])
        return 0
