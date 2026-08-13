"""core/pg_mirror.py — capa de ESCRITURA a Postgres, la única base del sistema.

Motores y jobs escriben acá y solo acá: no hay dual-write, ni flags, ni espejo
descartable. Todas las funciones son INCONDICIONALES y best-effort — nunca
levantan: cualquier error se loguea y el caller sigue (un fallo de SQL no puede
tumbar un motor ni bloquear una orden al broker). Ver docs/SQL.md.

API:
    write_native / write_snapshot  UPSERT por PK (snapshot vs batch)
    append_native                  INSERT append-only (streams sin PK natural)
    write_hist                     doc histórico intradía-mutable → mercado_hist
    merge_jsonb_native             merge atómico (`data || patch`) multi-writer
    read_native_doc                lectura de la columna jsonb `data` por PK
    prune_native / replace_native  retención por antigüedad / swap atómico

Semántica del upsert: se escriben SOLO las columnas presentes en cada fila (los
rows se agrupan por set de claves) → un escritor nunca pisa columnas que no le
pertenecen.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger("pg_mirror")

_CHUNK = 5000
_last_flush: dict[str, float] = {}


def doc_iso(v):
    """Conversión RECURSIVA datetime→ISO para guardar un doc anidado en jsonb.
    Tiene que ser deep: los datetimes anidados (ej. flujos de la master de renta
    fija) rompen json.dumps si solo se convierte el nivel top."""
    from datetime import datetime
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: doc_iso(x) for k, x in v.items()}
    if isinstance(v, list):
        return [doc_iso(x) for x in v]
    return v


def write_snapshot(table: str, key_cols: list[str], rows: list[dict],
                   min_interval: float = 0.0) -> int:
    """UPSERT para motores live. `min_interval`>0 descarta flushes más frecuentes
    que eso — usarlo SOLO si el caller reescribe el estado COMPLETO en cada
    iteración (ej. valores.py); un escritor de deltas (curvas.py) debe pasar 0 o
    perdería updates hasta el próximo cambio de precio."""
    if not rows:
        return 0
    if min_interval > 0:
        now = time.monotonic()
        if now - _last_flush.get(table, 0.0) < min_interval:
            return 0
        _last_flush[table] = now
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


def append_native(table: str, rows: list[dict]) -> int:
    """Append INCONDICIONAL. SQL es la única escritura."""
    return _append(table, rows) if rows else 0


def write_hist(coleccion: str, fecha_str: str, k: str, doc: dict) -> int:
    """Escribe un doc histórico intradía-mutable a `mercado_hist` (breakevens/forwards:
    la fila de HOY revive con precios live en cada tick del motor). Shape del row:
    `doc_iso` → jsonb, `fecha` date, `k` subclave — es el que espera `mercado_hist_sql`
    para leerlo. Best-effort: nunca levanta."""
    if not fecha_str:
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
        logger.error("pg_mirror write_hist %s: %s", coleccion, str(e).splitlines()[0][:200])
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


def write_native(table: str, key_cols: list[str], rows: list[dict]) -> int:
    """Upsert incondicional a SQL (el writer batch de los jobs)."""
    return _mirror(table, key_cols, rows) if rows else 0


def merge_jsonb_native(table: str, key_cols: list[str], key_vals: list, patch: dict) -> int:
    """Merge ATÓMICO (shallow) de `patch` en la columna jsonb `data` de UNA fila, vía
    `data = <table>.data || EXCLUDED.data` (ON CONFLICT). Update PARCIAL a nivel fila,
    sin read-modify-write → cero race entre procesos que tocan campos DISTINTOS del
    MISMO doc.

    Caso de uso: `mercado.options_metadata.config` es multi-writer — el motor
    (`engines/options.py`) escribe `tasa`/`expiries_disponibles`, el Manager
    (`/manager/options/expiries`) escribe `expiries`, y `update_opciones_tasa` la
    `tasa`. Con un write_native (que reescribe el `data` entero) se pisarían entre
    sí; con `||` cada uno mergea solo sus campos sin perder los del otro.

    Las `key_cols` se inyectan dentro de `data` (el doc lleva su propia clave, ej.
    `type`). Best-effort: nunca levanta."""
    if not patch:
        return 0
    try:
        import json
        from functools import partial

        from psycopg.types.json import Jsonb

        from core.postgres import get_pool

        data = dict(patch)
        for c, v in zip(key_cols, key_vals):
            data.setdefault(c, v)
        data = doc_iso(data)  # datetime → ISO (igual que el resto del mirror)

        dumps = partial(json.dumps, default=str)
        cols = list(key_cols) + ["data"]
        vals = list(key_vals) + [Jsonb(data, dumps=dumps)]
        sql = (
            f"INSERT INTO {table} ({','.join(cols)}) "
            f"VALUES ({','.join(['%s'] * len(cols))}) "
            f"ON CONFLICT ({','.join(key_cols)}) "
            f"DO UPDATE SET data = {table}.data || EXCLUDED.data"
        )
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql, tuple(vals))
        return 1
    except Exception as e:
        logger.error("pg_mirror merge_jsonb %s: %s", table, str(e).splitlines()[0][:200])
        return 0


def read_native_doc(table: str, key_cols: list[str], key_vals: list) -> dict:
    """Lee la columna jsonb `data` de UNA fila por su PK (ej. el motor de opciones
    leyendo su config: tasa/expiries). Devuelve {} si no hay fila. Best-effort."""
    try:
        from core.postgres import get_pool
        where = " AND ".join(f"{c} = %s" for c in key_cols)
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT data FROM {table} WHERE {where}", tuple(key_vals))
            row = cur.fetchone()
            return (row[0] or {}) if row else {}
    except Exception as e:
        logger.error("pg_mirror read_native_doc %s: %s", table, str(e).splitlines()[0][:200])
        return {}


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
    (ej. acreencias: grano no-único, surrogate PK IDENTITY). Best-effort: si falla,
    rollback (la tabla queda con el set anterior, nunca vacía). Las columnas IDENTITY
    se generan solas (no se pasan en los rows)."""
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
