"""scripts/diag_ops_sql_nulls.py — confirma los riesgos de traducción WHERE Mongo→SQL.

100% LECTURA sobre Postgres (tabla operaciones, ya cargada). Mide cuántos docs tienen
etapa/es_cierre NULL para validar dos reglas críticas de la migración de la vista OPERACIONES:

  * etapa: en Mongo `{"$ne": "solicitud"}` SÍ matchea los docs con etapa ausente. En SQL
    `etapa <> 'solicitud'` NO matchea NULL → hay que usar `etapa IS DISTINCT FROM 'solicitud'`.
    Si la mayoría tiene etapa NULL, confirmarlo es vital (si no, se perderían casi todos).
  * es_cierre: en Mongo `{es_cierre: false}` matchea solo el literal false. Si hay NULLs,
    `es_cierre = false` los excluye (correcto); `IS NOT TRUE` los incluiría (incorrecto).

    python -m scripts.diag_ops_sql_nulls
"""
from __future__ import annotations

from core.postgres import connect


def main() -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM operaciones")
        total = cur.fetchone()[0]
        print(f"operaciones: {total:,} filas\n")

        cur.execute("SELECT count(*) FROM operaciones WHERE etapa IS NULL")
        print(f"  etapa IS NULL          : {cur.fetchone()[0]:>9,}")
        cur.execute(
            "SELECT etapa, count(*) FROM operaciones GROUP BY etapa ORDER BY count(*) DESC"
        )
        print("  etapa por valor:")
        for v, n in cur.fetchall():
            print(f"    {v!s:24} {n:>9,}")

        print()
        cur.execute(
            "SELECT es_cierre, count(*) FROM operaciones "
            "GROUP BY es_cierre ORDER BY count(*) DESC"
        )
        print("  es_cierre por valor:")
        for v, n in cur.fetchall():
            print(f"    {v!s:24} {n:>9,}")
    print("\n→ Si etapa es mayormente NULL: confirmado, usar IS DISTINCT FROM 'solicitud'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
