"""diag_alquiler_cols.py — READ-ONLY: qué tiene REALMENTE portafolio.alquiler.

La tabla existe con un esquema que no matchea el código (falta id_cuenta). Este
diag muestra sus columnas, cuántas filas tiene y una muestra — para decidir si se
puede dropear+recrear (si está vacía / es un prototipo viejo) o hay que migrar.

Corré:  python -m scripts.diag_alquiler_cols
"""
from __future__ import annotations

from psycopg.rows import dict_row

from core.postgres import get_pool


def main() -> None:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'portafolio' AND table_name = 'alquiler'
            ORDER BY ordinal_position
        """)
        cols = cur.fetchall()
        print("== columnas de portafolio.alquiler ==")
        for c in cols:
            print(f"  {c['column_name']:<20} {c['data_type']}")

        cur.execute("SELECT count(*) AS n FROM portafolio.alquiler")
        n = cur.fetchone()["n"]
        print(f"\nfilas: {n}")

        if n:
            cur.execute("SELECT * FROM portafolio.alquiler LIMIT 5")
            print("\n== muestra (hasta 5 filas) ==")
            for r in cur.fetchall():
                print(f"  {r}")


if __name__ == "__main__":
    main()
