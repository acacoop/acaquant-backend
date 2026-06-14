"""scripts/crear_vista_aum_sql.py — crea la VISTA SQL `aum_sql` para el módulo /aum.

NO es una tabla ni copia datos: es un "filtro guardado" sobre `portafolio.tenencia`.
Expone la tenencia que cuenta como AuM (`aum = 'si'`, ver scripts/marcar_aum_tenencia)
con los nombres de columna que `api/services/portfolio_sql.py` ya entiende
(`fecha` → `fecha_snapshot`). Así /aum lee el dato corregido sin reescribir queries.

Para que /aum la use: AUM_SQL_TABLE=aum_sql + PORTFOLIO_SQL=1 (o ?_engine=sql).

Idempotente (CREATE OR REPLACE). Requiere que ya exista la columna `aum`
(correr antes `python -m scripts.marcar_aum_tenencia`).

Uso:
    python -m scripts.crear_vista_aum_sql
"""
from __future__ import annotations

from core.postgres import get_pool

_VIEW = """
CREATE OR REPLACE VIEW aum_sql AS
SELECT
    fecha AS fecha_snapshot,
    id_cuenta,
    unidad,
    cantidad,
    cuenta,
    precio,
    valuacion,
    NULL::text AS tipo_titulo
FROM portafolio.tenencia
WHERE aum = 'si'
"""


def main() -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema='portafolio' AND table_name='tenencia' "
                    "AND column_name='aum'")
        if cur.fetchone() is None:
            print("✗ Falta la columna `aum` en portafolio.tenencia. "
                  "Corré antes: python -m scripts.marcar_aum_tenencia")
            return 1
        cur.execute(_VIEW)
        conn.commit()
        cur.execute("SELECT count(*) AS n, count(DISTINCT fecha_snapshot) AS f FROM aum_sql")
        n, f = cur.fetchone()
    print(f"✅ vista `aum_sql` lista → {n:,} filas (aum='si') · {f} fechas")
    print("   Para activarla en /aum: AUM_SQL_TABLE=aum_sql + PORTFOLIO_SQL=1 + restart.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
