"""diag_db_espacio.py — READ-ONLY: estado de espacio/salud de la base (Supabase).

Para decidir qué monitorear y qué limpiar antes de que la base se sature.
Imprime:
  1. Tamaño total de la DB.
  2. Tamaño por SCHEMA (qué dominio ocupa más).
  3. Top tablas por tamaño total: tabla + índices, filas vivas/muertas (bloat),
     último autovacuum/analyze, y el ÚLTIMO DATO (max de su columna de fecha, si
     tiene una conocida).

No escribe nada. Corré:  python -m scripts.diag_db_espacio
"""
from __future__ import annotations

from psycopg.rows import dict_row

from core.postgres import get_pool

TOP_N = 30
# Columnas de "última actualización" candidatas, en orden de preferencia.
COLS_FECHA = ("updated_at", "ingestado_en", "generado_at", "ts", "ts_cierre",
              "fecha", "created_at")


def _fmt(n: float | int | None) -> str:
    if n is None:
        return "—"
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}PB"


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def main() -> None:
    total = _q("SELECT pg_database_size(current_database()) AS b")[0]["b"]
    print(f"== TAMAÑO TOTAL DE LA BASE: {_fmt(total)} ==\n")

    print("== POR SCHEMA ==")
    schemas = _q("""
        SELECT n.nspname AS schema,
               sum(pg_total_relation_size(c.oid)) AS bytes,
               count(*) AS tablas
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r'
          AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')
        GROUP BY n.nspname ORDER BY bytes DESC
    """)
    for s in schemas:
        print(f"  {s['schema']:<16}{_fmt(s['bytes']):>10}   ({s['tablas']} tablas)")

    print(f"\n== TOP {TOP_N} TABLAS POR TAMAÑO ==")
    tablas = _q("""
        SELECT n.nspname AS schema, c.relname AS tabla, c.oid AS oid,
               pg_total_relation_size(c.oid) AS total_b,
               pg_relation_size(c.oid) AS tabla_b,
               pg_indexes_size(c.oid) AS idx_b,
               s.n_live_tup, s.n_dead_tup,
               s.last_autovacuum, s.last_autoanalyze
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
        WHERE c.relkind = 'r'
          AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')
        ORDER BY pg_total_relation_size(c.oid) DESC
        LIMIT %s
    """, (TOP_N,))

    print(f"{'tabla':<40}{'total':>9}{'idx':>9}{'vivas':>10}{'muertas':>9}{'dead%':>6}  últ.dato")
    print("-" * 100)
    for t in tablas:
        live = t["n_live_tup"] or 0
        dead = t["n_dead_tup"] or 0
        deadpct = (dead / (live + dead) * 100) if (live + dead) else 0
        ult = _ultimo_dato(t["schema"], t["tabla"])
        nombre = f"{t['schema']}.{t['tabla']}"
        flag = "  <-- BLOAT" if deadpct > 20 and dead > 10000 else ""
        print(f"{nombre:<40}{_fmt(t['total_b']):>9}{_fmt(t['idx_b']):>9}"
              f"{live:>10}{dead:>9}{deadpct:>5.0f}%  {ult or '—'}{flag}")

    print("\nNota: 'dead%' alto (>20% con muchas filas) = candidato a VACUUM. "
          "'últ.dato' viejo en una tabla que debería actualizarse = feed a revisar.")


def _cols(schema: str, tabla: str) -> set[str]:
    rows = _q("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
    """, (schema, tabla))
    return {r["column_name"] for r in rows}


def _ultimo_dato(schema: str, tabla: str) -> str | None:
    cols = _cols(schema, tabla)
    col = next((c for c in COLS_FECHA if c in cols), None)
    if not col:
        return None
    try:
        # schema/tabla/col vienen de pg_catalog (no input de usuario) → seguro.
        r = _q(f'SELECT max("{col}") AS m FROM "{schema}"."{tabla}"')
        m = r[0]["m"] if r else None
        return str(m)[:19] if m is not None else None
    except Exception:
        return None


if __name__ == "__main__":
    main()
