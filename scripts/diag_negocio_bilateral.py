"""diag READ-ONLY — ¿por qué la vista no muestra las operaciones de mercado
'bilateral' de esta semana si están en SQL?

Mira las DOS tablas que alimentan las vistas de operaciones y vuelca, para los
últimos N días, cómo está guardado 'bilateral' y los campos por los que las
vistas filtran — así vemos qué filtro las deja afuera SIN adivinar:

  operaciones.operaciones      → vista OPERACIONES / MOVIMIENTOS. Filtra por
                                 moneda, es_cierre=false, mercado, y la regla FCI
                                 bilateral (operacion/etapa). Fecha = concertacion.
  operaciones.negocio_movimientos → vista NEGOCIO (boletos por día). Fecha = fecha.

Uso:
    python -m scripts.diag_negocio_bilateral
    python -m scripts.diag_negocio_bilateral --dias 15 --patron bilat

Solo hace SELECT. Borrar tras cerrar el tema (REGLA #5).
"""
from __future__ import annotations

import argparse

from psycopg.rows import dict_row

from core.postgres import get_pool


def _cols(cur, schema: str, table: str) -> list[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
        (schema, table))
    # cursor con row_factory=dict_row → cada fila es dict, no tupla.
    return [r["column_name"] for r in cur.fetchall()]


def _run(cur, titulo: str, sql: str, params: dict) -> None:
    print(f"\n--- {titulo} ---")
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
        if not rows:
            print("   (0 filas)")
        for r in rows:
            print("  ", {k: v for k, v in r.items()})
    except Exception as e:
        print("   ERROR:", type(e).__name__, e)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=10)
    ap.add_argument("--patron", default="bilat", help="substring (ILIKE) del mercado a buscar")
    args = ap.parse_args()
    pat = f"%{args.patron}%"

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        # ── operaciones.operaciones (vista OPERACIONES / MOVIMIENTOS) ──
        print("=" * 72)
        print("TABLA operaciones.operaciones   (últimos", args.dias, "días por concertacion)")
        cols = _cols(cur, "operaciones", "operaciones")
        print("COLUMNAS:", cols)

        _run(cur, "mercados distintos (recientes) + conteo",
             "SELECT mercado, count(*) n, max(concertacion) ult "
             "FROM operaciones.operaciones "
             "WHERE concertacion >= current_date - %(d)s "
             "GROUP BY mercado ORDER BY n DESC",
             {"d": args.dias})

        _run(cur, f"filas mercado ILIKE '{pat}' por (fecha, moneda, es_cierre, operacion, etapa)",
             "SELECT to_char(concertacion,'YYYY-MM-DD') fecha, mercado, moneda, es_cierre, "
             "operacion, etapa, count(*) n "
             "FROM operaciones.operaciones "
             "WHERE concertacion >= current_date - %(d)s AND mercado ILIKE %(pat)s "
             "GROUP BY 1,2,3,4,5,6 ORDER BY 1 DESC, n DESC",
             {"d": args.dias, "pat": pat})

        _run(cur, "muestra de filas bilaterales recientes (5)",
             "SELECT to_char(concertacion,'YYYY-MM-DD') fecha, mercado, moneda, es_cierre, "
             "operacion, etapa, denominacion, ingestado_en "
             "FROM operaciones.operaciones "
             "WHERE concertacion >= current_date - %(d)s AND mercado ILIKE %(pat)s "
             "ORDER BY concertacion DESC LIMIT 5",
             {"d": args.dias, "pat": pat})

        # ── operaciones.negocio_movimientos (vista NEGOCIO) ──
        print("\n" + "=" * 72)
        print("TABLA operaciones.negocio_movimientos   (últimos", args.dias, "días por fecha)")
        ncols = _cols(cur, "operaciones", "negocio_movimientos")
        print("COLUMNAS:", ncols)
        # Busca 'bilateral' en cualquier columna de texto candidata (mercado/informacion/…).
        text_cols = [c for c in ncols if c in
                     ("mercado", "informacion", "operacion", "etapa", "tipo", "detalle", "nivel_3")]
        for col in text_cols:
            _run(cur, f"negocio_movimientos: {col} ILIKE '{pat}' por fecha (recientes)",
                 f"SELECT to_char(fecha,'YYYY-MM-DD') fecha, {col}, count(*) n "
                 f"FROM operaciones.negocio_movimientos "
                 f"WHERE fecha >= current_date - %(d)s AND {col} ILIKE %(pat)s "
                 f"GROUP BY 1,2 ORDER BY 1 DESC, n DESC",
                 {"d": args.dias, "pat": pat})
        _run(cur, "negocio_movimientos: conteo total por fecha (recientes)",
             "SELECT to_char(fecha,'YYYY-MM-DD') fecha, count(*) n, max(ingestado_en) ult "
             "FROM operaciones.negocio_movimientos "
             "WHERE fecha >= current_date - %(d)s GROUP BY 1 ORDER BY 1 DESC",
             {"d": args.dias})


if __name__ == "__main__":
    main()
