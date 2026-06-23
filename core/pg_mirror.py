"""core/pg_mirror.py — dual-write best-effort Mongo→Postgres (Fase 2, capa MERCADO).

Los motores/jobs llaman a estas funciones DESPUÉS de su write a Mongo. Con el
flag apagado son no-op instantáneo (cero cambio de comportamiento). NUNCA
levantan: cualquier error se loguea y el caller sigue — Mongo es la base
operativa, Postgres es espejo descartable (ver docs/SQL.md).

Flags (env, default OFF):
    SNAPSHOT_SQL=1       → motores live: market_snapshot (valores/curvas) +
                           mercado_hist de los históricos intradía-mutables
                           (breakevens/forwards, vía mirror_hist)
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


def doc_iso(v):
    """Conversión RECURSIVA datetime→ISO para guardar docs Mongo en jsonb.
    Tiene que ser deep: los datetimes anidados (ej. flujos de BondsMaster)
    rompen json.dumps si solo se convierte el nivel top."""
    from datetime import datetime
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: doc_iso(x) for k, x in v.items()}
    if isinstance(v, list):
        return [doc_iso(x) for x in v]
    return v


def prune_job(table: str, col: str, days: int) -> int:
    """Retención best-effort en el espejo (flag MERCADO_SQL_WRITE): borra filas con
    `col` más viejo que `days` días. Para tablas cuya fuente Mongo tiene TTL (ej.
    News.Headlines, 2 días) — sin esto el espejo acumularía lo que Mongo ya borró."""
    if not jobs_on():
        return 0
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {table} WHERE {col} < now() - %s * interval '1 day'",
                (days,),
            )
            return cur.rowcount or 0
    except Exception as e:
        logger.error("pg_mirror prune %s: %s", table, str(e).splitlines()[0][:200])
        return 0


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


def _append(table: str, rows: list[dict]) -> int:
    """INSERT append-only (sin upsert). Para STREAMS como TimeSales / options_data: cada fila
    es nueva, sin PK natural → no hay ON CONFLICT. dict/list → Jsonb (columnas jsonb, ej. el
    tick crudo de options_data). Best-effort: nunca levanta."""
    try:
        import json
        from functools import partial

        from psycopg.types.json import Jsonb

        from core.postgres import get_pool

        dumps = partial(json.dumps, default=str)  # datetimes residuales dentro de jsonb → str

        def _adapt(v):
            return Jsonb(v, dumps=dumps) if isinstance(v, dict | list) else v

        grupos: dict[tuple, list[tuple]] = {}
        for r in rows:
            cols = tuple(r.keys())
            grupos.setdefault(cols, []).append(tuple(_adapt(r[c]) for c in cols))
        n = 0
        with get_pool().connection() as conn, conn.cursor() as cur:
            for cols, vals in grupos.items():
                sql = (f"INSERT INTO {table} ({','.join(cols)}) "
                       f"VALUES ({','.join(['%s'] * len(cols))})")
                for i in range(0, len(vals), _CHUNK):
                    cur.executemany(sql, vals[i:i + _CHUNK])
                n += len(vals)
        return n
    except Exception as e:
        logger.error("pg_mirror append %s: %s", table, str(e).splitlines()[0][:200])
        return 0


def append_snapshot(table: str, rows: list[dict]) -> int:
    """Append gateado por SNAPSHOT_SQL (motor en modo DUAL-write Mongo+SQL)."""
    return _append(table, rows) if (snapshots_live_on() and rows) else 0


def append_native(table: str, rows: list[dict]) -> int:
    """Append INCONDICIONAL (motor SQL-native, sin Mongo). SQL es la única escritura."""
    return _append(table, rows) if rows else 0


def mirror_hist(coleccion: str, fecha_str: str, k: str, doc: dict) -> int:
    """Espejo de un doc histórico intradía-mutable a `mercado_hist`, desde un MOTOR
    (flag SNAPSHOT_SQL). BreakevensHistorico / ForwardsHistorico reviven la fila de
    HOY con precios live → el sync horario la deja stale; el motor la mantiene
    fresca acá. Aplica `doc_iso` (datetime→isoformat) IGUAL que `sync._jsonb` para
    que `mercado_hist_sql` lea idéntico a Mongo. No-op con el flag apagado."""
    if not snapshots_live_on() or not fecha_str:
        return 0
    try:
        from datetime import date
        row = {
            "coleccion": coleccion,
            "fecha": date.fromisoformat(str(fecha_str)[:10]),
            "k": k or "",
            "data": doc_iso(doc),
        }
        return _mirror("mercado_hist", ["coleccion", "fecha", "k"], [row])
    except Exception as e:
        logger.error("pg_mirror hist %s: %s", coleccion, str(e).splitlines()[0][:200])
        return 0


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


# ── SQL-NATIVE (decommission Mongo): upsert/prune INCONDICIONALES ─────────────
# Para jobs que ya NO escriben Mongo (la fuente es SQL). A diferencia de mirror_job/
# prune_job (gateados por MERCADO_SQL_WRITE = "dual-write"), estos siempre escriben.
def write_native(table: str, key_cols: list[str], rows: list[dict]) -> int:
    """Upsert incondicional a SQL (job SQL-native, sin Mongo)."""
    return _mirror(table, key_cols, rows) if rows else 0


def prune_native(table: str, col: str, days: int) -> int:
    """Retención incondicional (job SQL-native): borra filas con `col` > `days` días."""
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {table} WHERE {col} < now() - %s * interval '1 day'", (days,))
            return cur.rowcount or 0
    except Exception as e:
        logger.error("pg_mirror prune_native %s: %s", table, str(e).splitlines()[0][:200])
        return 0


def replace_native(table: str, rows: list[dict]) -> int:
    """Swap atómico de TODA la tabla (TRUNCATE + INSERT en una transacción), para
    precomputes que reemplazan el set entero — sin PK natural sobre la que upsertar
    (ej. acreencias: grano no-único, surrogate PK IDENTITY). Análogo a Mongo
    reemplazar_coleccion_atomico. Best-effort: si falla, rollback y Mongo queda como
    fuente. Las columnas IDENTITY se generan solas (no se pasan en los rows)."""
    if not rows:
        return 0
    try:
        import json
        from functools import partial

        from psycopg.types.json import Jsonb

        from core.postgres import get_pool

        dumps = partial(json.dumps, default=str)

        def _adapt(v):
            return Jsonb(v, dumps=dumps) if isinstance(v, dict | list) else v

        grupos: dict[tuple, list[tuple]] = {}
        for r in rows:
            cols = tuple(r.keys())
            grupos.setdefault(cols, []).append(tuple(_adapt(r[c]) for c in cols))
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(f"TRUNCATE {table}")
            for cols, vals in grupos.items():
                sql = (f"INSERT INTO {table} ({','.join(cols)}) "
                       f"VALUES ({','.join(['%s'] * len(cols))})")
                for i in range(0, len(vals), _CHUNK):
                    cur.executemany(sql, vals[i:i + _CHUNK])
        return len(rows)
    except Exception as e:
        logger.error("pg_mirror replace_native %s: %s", table, str(e).splitlines()[0][:200])
        return 0
