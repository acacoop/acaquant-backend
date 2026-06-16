"""scripts/diag_sql_inventario.py — foto REAL de Supabase/Postgres.

Lista las tablas que EXISTEN de verdad (pg_catalog) por schema, con conteo exacto de
filas + tamaño en disco. Sirve para ver qué hay realmente vs el sql/schema.sql (que puede
mentir: tablas huérfanas, tablas que el schema no refleja). Read-only.

Uso (en el Droplet):  python -m scripts.diag_sql_inventario
"""
from core.postgres import get_pool


def main() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT n.nspname AS schema, c.relname AS tabla,
                   pg_size_pretty(pg_total_relation_size(c.oid)) AS tamano
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r'
              AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
            ORDER BY n.nspname, c.relname
        """)
        filas = cur.fetchall()
        schemas = sorted({f[0] for f in filas})
        print(f"{len(filas)} tablas en {len(schemas)} schemas: {', '.join(schemas)}\n")

        actual = None
        total_filas = 0
        for schema, tabla, tamano in filas:
            if schema != actual:
                print(f"\n── schema {schema} " + "─" * (40 - len(schema)))
                actual = schema
            try:
                cur.execute(f'SELECT count(*) FROM "{schema}"."{tabla}"')
                n = cur.fetchone()[0]
                total_filas += n
                flag = "  ← VACÍA" if n == 0 else ""
                print(f"  {tabla:<34} {n:>12,}   {tamano:>10}{flag}")
            except Exception as e:
                print(f"  {tabla:<34} {'err:' + type(e).__name__:>12}   {tamano:>10}")

        print(f"\nTOTAL: {total_filas:,} filas en {len(filas)} tablas.")
        print("Las VACÍAS son candidatas a tabla huérfana (creada y nunca usada / dropeada a medias).")


if __name__ == "__main__":
    main()
