"""diag_tenencia_hd.py — READ-ONLY: por qué "Títulos en Alquiler" está vacío.

La vista lee portafolio.tenencia de las cuentas propias 100/255/256 con aum='si'
al último día. Si no muestra nada, es que esa query no trae filas. Este diag mide
exactamente eso:
  1. Última fecha global de portafolio.tenencia.
  2. Para 100/255/256: cuántas filas hay, desglosado por flag `aum`, y hasta qué
     fecha — para ver si faltan, o están con aum='no', o directamente no están.
  3. La query EXACTA de la vista (aum='si', esas cuentas) → cuántas filas trae.

Corré:  python -m scripts.diag_tenencia_hd
"""
from __future__ import annotations

from psycopg.rows import dict_row

from core.postgres import get_pool

CUENTAS = ["100", "255", "256"]


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def main() -> None:
    g = _q("SELECT max(fecha) AS m, count(*) AS n FROM portafolio.tenencia")[0]
    print(f"== portafolio.tenencia (GLOBAL): {g['n']} filas · última fecha {g['m']} ==\n")

    print("== CUENTAS PROPIAS 100/255/256 — filas por cuenta y flag aum ==")
    filas = _q("""
        SELECT id_cuenta, aum, count(*) AS n, max(fecha) AS ult
        FROM portafolio.tenencia
        WHERE id_cuenta = ANY(%s)
        GROUP BY id_cuenta, aum ORDER BY id_cuenta, aum
    """, (CUENTAS,))
    if not filas:
        print("  (NINGUNA fila de esas cuentas en portafolio.tenencia → la vista no puede mostrar nada)")
    for r in filas:
        print(f"  cuenta {r['id_cuenta']:<6} aum={str(r['aum']):<5} {r['n']:>7} filas · última {r['ult']}")

    print("\n== LA QUERY EXACTA DE LA VISTA (aum='si', esas cuentas) ==")
    v = _q("""
        SELECT max(fecha) AS ult FROM portafolio.tenencia
        WHERE aum = 'si' AND id_cuenta = ANY(%s)
    """, (CUENTAS,))
    ult = v[0]["ult"] if v else None
    print(f"  última fecha con aum='si': {ult}")
    if ult:
        n = _q("""
            SELECT count(*) AS n FROM portafolio.tenencia
            WHERE fecha = %s AND aum = 'si' AND id_cuenta = ANY(%s)
              AND cantidad <> 0
        """, (ult, CUENTAS))[0]["n"]
        print(f"  posiciones (cantidad<>0) ese día: {n}  → eso es lo que ve la vista")
    else:
        print("  → la vista queda VACÍA (no hay último día). Causa: esas cuentas no")
        print("    tienen tenencia con aum='si'. Ver el desglose de arriba.")

    # Muestra qué valores de aum existen (por si es NULL / otro string).
    print("\n== valores distintos de `aum` en la tabla ==")
    for r in _q("SELECT aum, count(*) AS n FROM portafolio.tenencia GROUP BY aum ORDER BY n DESC"):
        print(f"  aum={r['aum']!r:<8} {r['n']} filas")


if __name__ == "__main__":
    main()
