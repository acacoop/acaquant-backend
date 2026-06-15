"""scripts/diag_pnl_cartera_tipotitulo.py — READ-ONLY. Por qué el PnL no ÷100.

Muestra, para el último snapshot de portafolio.tenencia (aum='si'):
  1) qué valores de `cartera` existen + cuántas filas (para ver si los bonos son
     HD/DL/ARS o caen en otra cartera que el normalizer no divide).
  2) cuántas filas tienen `tipo_titulo` NULL (el campo del que dependía antes).
  3) ejemplos de filas que PARECEN renta fija (precio entre 10 y 10000, típico de
     bonos en paridad) con su cartera/tipo_titulo — para cazar el caso roto.

    python -m scripts.diag_pnl_cartera_tipotitulo
"""
from __future__ import annotations

from core.postgres import get_pool


def main() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        f = cur.fetchone()[0]
        print(f"\n=== último snapshot tenencia (aum='si'): {f} ===\n")

        cur.execute(
            "SELECT COALESCE(cartera, '(NULL)') AS cartera, count(*), "
            "       count(*) FILTER (WHERE tipo_titulo IS NULL) AS tt_null "
            "FROM portafolio.tenencia WHERE fecha = %s AND aum = 'si' "
            "GROUP BY cartera ORDER BY count(*) DESC", (f,))
        print(f"{'CARTERA':<22}{'FILAS':>8}{'tipo_titulo NULL':>20}")
        for cartera, n, tt_null in cur.fetchall():
            print(f"{cartera:<22}{n:>8}{tt_null:>20}")

        # Ejemplos que LUCEN renta fija (precio en rango paridad) — ¿qué cartera tienen?
        cur.execute(
            "SELECT unidad, cartera, tipo_titulo, cantidad, precio, valuacion "
            "FROM portafolio.tenencia "
            "WHERE fecha = %s AND aum = 'si' AND precio BETWEEN 10 AND 10000 "
            "ORDER BY valuacion DESC NULLS LAST LIMIT 20", (f,))
        print("\nEjemplos que lucen renta fija (precio 10-10000) — mirá cartera/tipo_titulo:")
        print(f"  {'unidad':<26}{'cartera':<14}{'tipo_titulo':<28}{'precio':>10}{'valuacion':>16}")
        for u, cart, tt, _cant, prec, val in cur.fetchall():
            print(f"  {(u or '')[:25]:<26}{(cart or '(NULL)')[:13]:<14}"
                  f"{(tt or '(NULL)')[:27]:<28}{float(prec or 0):>10.2f}{float(val or 0):>16,.2f}")
    print()


if __name__ == "__main__":
    main()
