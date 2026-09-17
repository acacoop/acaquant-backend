"""Fotografia read-only del catalogo PostgreSQL para auditar la arquitectura SQL.

No lee filas de negocio: consulta exclusivamente pg_catalog y vistas pg_stat_*.
Cada bloque corre en su propia transaccion READ ONLY, con timeouts cortos, y
termina en ROLLBACK. La salida JSON no incluye credenciales ni textos de queries
capturados por pg_stat_statements.

Uso (Droplet, desde la raiz del repo):
    python -m scripts.diag_auditoria_sql > /tmp/acaquant_sql_audit.json
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import NamedTuple

from core import schema_sql


class Consulta(NamedTuple):
    nombre: str
    sql: str


CONSULTAS = (
    Consulta("servidor", """
        SELECT current_setting('server_version') AS server_version,
               current_setting('server_version_num')::integer AS server_version_num,
               pg_postmaster_start_time() AS postmaster_start_time,
               d.stats_reset
          FROM pg_stat_database d
         WHERE d.datname = current_database()
    """),
    Consulta("schemas", """
        SELECT n.nspname AS schema,
               pg_get_userbyid(n.nspowner) AS owner
          FROM pg_namespace n
         WHERE n.nspname <> ALL(%s)
           AND n.nspname NOT LIKE 'pg_temp_%%'
           AND n.nspname NOT LIKE 'pg_toast_temp_%%'
         ORDER BY n.nspname
    """),
    Consulta("relaciones", """
        SELECT n.nspname AS schema,
               c.relname AS relacion,
               c.relkind,
               c.relpersistence,
               c.relispartition,
               c.relrowsecurity,
               c.relforcerowsecurity,
               pg_get_userbyid(c.relowner) AS owner,
               COALESCE(c.reltuples, 0)::bigint AS filas_estimadas,
               pg_total_relation_size(c.oid) AS bytes_total,
               pg_relation_size(c.oid) AS bytes_heap,
               pg_indexes_size(c.oid) AS bytes_indices,
               c.reloptions,
               obj_description(c.oid, 'pg_class') AS comentario
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = ANY(%s)
           AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
         ORDER BY n.nspname, c.relkind, c.relname
    """),
    Consulta("columnas", """
        SELECT n.nspname AS schema,
               c.relname AS tabla,
               a.attnum AS posicion,
               a.attname AS columna,
               pg_catalog.format_type(a.atttypid, a.atttypmod) AS tipo,
               a.attnotnull AS not_null,
               a.attidentity AS identidad,
               a.attgenerated AS generada,
               pg_get_expr(d.adbin, d.adrelid) AS valor_default,
               col_description(c.oid, a.attnum) AS comentario
          FROM pg_attribute a
          JOIN pg_class c ON c.oid = a.attrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
          LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
         WHERE n.nspname = ANY(%s)
           AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
           AND a.attnum > 0
           AND NOT a.attisdropped
         ORDER BY n.nspname, c.relname, a.attnum
    """),
    Consulta("constraints", """
        SELECT n.nspname AS schema,
               c.relname AS tabla,
               con.conname AS constraint,
               con.contype AS tipo,
               con.condeferrable AS diferible,
               con.condeferred AS inicialmente_diferida,
               con.convalidated AS validada,
               nr.nspname AS schema_referido,
               cr.relname AS tabla_referida,
               pg_get_constraintdef(con.oid, true) AS definicion
          FROM pg_constraint con
          JOIN pg_class c ON c.oid = con.conrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
          LEFT JOIN pg_class cr ON cr.oid = con.confrelid
          LEFT JOIN pg_namespace nr ON nr.oid = cr.relnamespace
         WHERE n.nspname = ANY(%s)
         ORDER BY n.nspname, c.relname, con.contype, con.conname
    """),
    Consulta("indices", """
        SELECT n.nspname AS schema,
               c.relname AS tabla,
               i.relname AS indice,
               x.indisprimary AS primario,
               x.indisunique AS unico,
               x.indisexclusion AS exclusion,
               x.indisvalid AS valido,
               x.indisready AS listo,
               pg_relation_size(i.oid) AS bytes,
               COALESCE(s.idx_scan, 0) AS scans,
               pg_get_expr(x.indpred, x.indrelid) AS predicado,
               pg_get_indexdef(i.oid) AS definicion
          FROM pg_index x
          JOIN pg_class c ON c.oid = x.indrelid
          JOIN pg_class i ON i.oid = x.indexrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
          LEFT JOIN pg_stat_user_indexes s ON s.indexrelid = i.oid
         WHERE n.nspname = ANY(%s)
         ORDER BY n.nspname, c.relname, i.relname
    """),
    Consulta("estadisticas_tablas", """
        SELECT schemaname AS schema,
               relname AS tabla,
               n_live_tup,
               n_dead_tup,
               n_mod_since_analyze,
               seq_scan,
               seq_tup_read,
               idx_scan,
               idx_tup_fetch,
               n_tup_ins,
               n_tup_upd,
               n_tup_del,
               n_tup_hot_upd,
               last_vacuum,
               last_autovacuum,
               last_analyze,
               last_autoanalyze,
               vacuum_count,
               autovacuum_count,
               analyze_count,
               autoanalyze_count
          FROM pg_stat_user_tables
         WHERE schemaname = ANY(%s)
         ORDER BY schemaname, relname
    """),
    Consulta("particiones", """
        SELECT np.nspname AS parent_schema,
               p.relname AS parent,
               nc.nspname AS child_schema,
               c.relname AS child,
               pg_get_expr(c.relpartbound, c.oid) AS limite
          FROM pg_inherits h
          JOIN pg_class p ON p.oid = h.inhparent
          JOIN pg_namespace np ON np.oid = p.relnamespace
          JOIN pg_class c ON c.oid = h.inhrelid
          JOIN pg_namespace nc ON nc.oid = c.relnamespace
         WHERE np.nspname = ANY(%s) OR nc.nspname = ANY(%s)
         ORDER BY np.nspname, p.relname, nc.nspname, c.relname
    """),
    Consulta("secuencias", """
        SELECT n.nspname AS schema,
               c.relname AS secuencia,
               format_type(s.seqtypid, NULL) AS tipo,
               s.seqstart AS inicio,
               s.seqincrement AS incremento,
               s.seqmin AS minimo,
               s.seqmax AS maximo,
               s.seqcache AS cache,
               s.seqcycle AS ciclica
          FROM pg_sequence s
          JOIN pg_class c ON c.oid = s.seqrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = ANY(%s)
         ORDER BY n.nspname, c.relname
    """),
    Consulta("triggers", """
        SELECT n.nspname AS schema,
               c.relname AS tabla,
               t.tgname AS trigger,
               t.tgenabled AS habilitado,
               pg_get_triggerdef(t.oid, true) AS definicion
          FROM pg_trigger t
          JOIN pg_class c ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = ANY(%s)
           AND NOT t.tgisinternal
         ORDER BY n.nspname, c.relname, t.tgname
    """),
        Consulta("vistas", """
           SELECT n.nspname AS schema,
                c.relname AS vista,
                c.relkind,
                pg_get_viewdef(c.oid, true) AS definicion
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = ANY(%s)
             AND c.relkind IN ('v', 'm')
            ORDER BY n.nspname, c.relname
        """),
    Consulta("politicas_rls", """
        SELECT schemaname AS schema,
               tablename AS tabla,
               policyname AS politica,
               permissive,
               roles,
               cmd,
               qual,
               with_check
          FROM pg_policies
         WHERE schemaname = ANY(%s)
         ORDER BY schemaname, tablename, policyname
    """),
    Consulta("extensiones", """
        SELECT e.extname AS extension,
               e.extversion AS version,
               n.nspname AS schema
          FROM pg_extension e
          JOIN pg_namespace n ON n.oid = e.extnamespace
         ORDER BY e.extname
    """),
)

_SCHEMAS_SISTEMA = ["pg_catalog", "information_schema", "pg_toast"]


def _connect():
    from core.postgres import connect

    return connect()


def _params(consulta: Consulta, schemas: list[str]) -> tuple:
    if consulta.nombre == "servidor" or consulta.nombre == "extensiones":
        return ()
    if consulta.nombre == "schemas":
        return (_SCHEMAS_SISTEMA,)
    if consulta.nombre == "particiones":
        return (schemas, schemas)
    return (schemas,)


def _filas(cur) -> list[dict]:
    columnas = [d.name for d in cur.description]
    return [dict(zip(columnas, fila, strict=True)) for fila in cur.fetchall()]


def fotografiar() -> dict:
    schemas = sorted(schema_sql.schemas())
    resultado = {
        "generado_at": datetime.now(UTC),
        "alcance": "solo pg_catalog y vistas pg_stat; sin filas de negocio",
        "schema_esperado": {
            "schemas": schemas,
            "tablas": sorted(schema_sql.tablas()),
        },
        "secciones": {},
        "errores": {},
    }

    conn = _connect()
    try:
        for consulta in CONSULTAS:
            try:
                with conn.cursor() as cur:
                    cur.execute("BEGIN READ ONLY")
                    cur.execute("SET LOCAL statement_timeout = '15s'")
                    cur.execute("SET LOCAL lock_timeout = '2s'")
                    cur.execute(consulta.sql, _params(consulta, schemas))
                    resultado["secciones"][consulta.nombre] = _filas(cur)
            except Exception as exc:
                resultado["errores"][consulta.nombre] = {
                    "tipo": type(exc).__name__,
                    "mensaje": str(exc).splitlines()[0][:300],
                }
            finally:
                conn.rollback()
    finally:
        conn.close()
    return resultado


def main() -> int:
    print(json.dumps(fotografiar(), ensure_ascii=True, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())