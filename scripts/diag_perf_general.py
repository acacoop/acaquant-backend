"""Diag PERF GENERAL (read-only) — mide en prod qué es lento de verdad.

Correr en el Droplet:  python -m scripts.diag_perf_general

Qué mide (todo read-only, sin tocar datos):
  1) pg_stat_statements: top 15 queries por tiempo TOTAL acumulado y por tiempo
     promedio — la foto real de dónde se va el tiempo de la DB (si la extensión
     está habilitada; en Supabase suele estarlo).
  2) Tablas con más seq_scan vs idx_scan (candidatas a índice faltante) + tamaño.
  3) EXPLAIN ANALYZE de los candidatos que dejó la auditoría estática 2026-07-28:
     - max(fecha) de portafolio.tenencia WHERE aum='si' (3 call sites: pnl_sql:167,
       comercial_sql:147 y :187) — la tabla NO tiene índice por (aum, fecha).
     - AuM por cuenta del último snapshot (query base del tablero comercial).
"""
from __future__ import annotations

import time

from core.postgres import get_pool

LINEA = "─" * 78


def _explain(cur, titulo: str, sql: str, params=None) -> None:
    print(f"\n{LINEA}\n▶ {titulo}\n{LINEA}")
    t0 = time.perf_counter()
    cur.execute(f"EXPLAIN (ANALYZE, BUFFERS) {sql}", params)
    plan = "\n".join(r[0] for r in cur.fetchall())
    ms = (time.perf_counter() - t0) * 1000
    print(plan)
    print(f"⏱  wall total (explain+exec): {ms:.0f}ms")


def main() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        # 1) pg_stat_statements — la verdad medida de prod.
        # En Supabase la extensión vive en el schema `extensions` (no en public),
        # así que resolvemos el schema real en vez de asumir el search_path.
        print(f"\n{LINEA}\n▶ 1) TOP QUERIES por tiempo total (pg_stat_statements)\n{LINEA}")
        try:
            cur.execute(
                """
                SELECT n.nspname
                FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
                WHERE e.extname = 'pg_stat_statements'
                """
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("extensión pg_stat_statements no instalada")
            pss = f'"{row[0]}".pg_stat_statements'
            cur.execute(
                f"""
                SELECT round(total_exec_time)::bigint AS total_ms, calls,
                       round(mean_exec_time, 1) AS mean_ms,
                       rows / greatest(calls, 1) AS rows_x_call,
                       left(regexp_replace(query, '\\s+', ' ', 'g'), 130) AS q
                FROM {pss}
                WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
                  AND query NOT ILIKE '%pg_stat%' AND query NOT ILIKE 'EXPLAIN%'
                ORDER BY total_exec_time DESC LIMIT 15
                """
            )
            print(f"{'total_ms':>10} {'calls':>8} {'mean_ms':>9} {'rows/c':>7}  query")
            for total_ms, calls, mean_ms, rpc, q in cur.fetchall():
                print(f"{total_ms:>10} {calls:>8} {mean_ms:>9} {rpc:>7}  {q}")
            print("\n  (mismo ranking por MEAN — las lentas por request):")
            cur.execute(
                f"""
                SELECT round(mean_exec_time, 1) AS mean_ms, calls,
                       left(regexp_replace(query, '\\s+', ' ', 'g'), 130) AS q
                FROM {pss}
                WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
                  AND calls >= 20 AND query NOT ILIKE '%pg_stat%'
                ORDER BY mean_exec_time DESC LIMIT 10
                """
            )
            for mean_ms, calls, q in cur.fetchall():
                print(f"  {mean_ms:>9}ms × {calls:<7} {q}")
        except Exception as e:  # extensión no habilitada / sin permisos
            conn.rollback()
            print(f"  pg_stat_statements NO disponible: {e}")

        # 2) seq scans — índices faltantes
        print(f"\n{LINEA}\n▶ 2) Tablas con más SEQ SCAN (candidatas a índice)\n{LINEA}")
        cur.execute(
            """
            SELECT schemaname || '.' || relname AS tabla, seq_scan, idx_scan,
                   n_live_tup,
                   pg_size_pretty(pg_total_relation_size(relid)) AS size
            FROM pg_stat_user_tables
            WHERE seq_scan > 100 AND n_live_tup > 10000
            ORDER BY seq_scan * n_live_tup DESC LIMIT 12
            """
        )
        print(f"{'tabla':<45} {'seq_scan':>9} {'idx_scan':>9} {'filas':>10} {'size':>9}")
        for tabla, seq, idx, n, size in cur.fetchall():
            print(f"{tabla:<45} {seq:>9} {idx or 0:>9} {n:>10} {size:>9}")

        # 3) candidatos concretos de la auditoría estática
        _explain(cur, "3a) max(fecha) tenencia aum='si' — pnl_sql:167 / comercial_sql:147,187",
                 "SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        f = cur.fetchone()[0]
        if f:
            _explain(cur, "3b) AuM por cuenta del snapshot (base del tablero comercial)",
                     "SELECT id_cuenta, SUM(valuacion) FROM portafolio.tenencia "
                     "WHERE fecha = %(f)s AND aum = 'si' GROUP BY id_cuenta", {"f": f})

    print(f"\n{LINEA}\nListo. Pegame el output y decidimos si algún índice vale la pena.\n{LINEA}")


if __name__ == "__main__":
    main()
