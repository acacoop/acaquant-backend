"""diag_ops_cartera — ¿por qué campo se une Operaciones con el catálogo de Assets?

READ-ONLY. Necesario para agregar el filtro CARTERA a NEGOCIO → Operaciones
(pedido del user 2026-07-21): `operaciones.operaciones` NO tiene `unidad` (la
clave con la que el resto del sistema une contra `portafolio.assets`), tiene
`instrumento`. Y `assets` tiene TRES campos candidatos: `unidad` (PK),
`ticker` e `instrumento`.

Cuál es el bueno NO se adivina (REGLA #2): esto lo MIDE, y lo mide por
VOLUMEN operado, no por cantidad de valores distintos — un join que cubre 200
instrumentos raros pero deja afuera los 5 que mueven el 80% del volumen es
inservible para un filtro.

Correr en el Droplet:
    python -m scripts.diag_ops_cartera
    python -m scripts.diag_ops_cartera --meses 12

Qué imprime:
  1. Volumen y boletos del período, y cuántos instrumentos distintos hay.
  2. Por cada candidato (unidad / ticker / instrumento de assets, exacto y
     normalizado): % de VOLUMEN y % de boletos que quedarían con cartera.
  3. El TOP de instrumentos por volumen que NO matchean con el mejor
     candidato (para ver si es un problema real o marginal).
  4. Las carteras que aparecerían en el filtro, con su volumen.

Con eso decidimos el join y se codea el filtro. Este diag se BORRA cuando el
tema cierre (REGLA #5).
"""
from __future__ import annotations

import argparse

from core.postgres import get_pool

_NORM = "upper(btrim({}))"


def _q(cur, sql: str, params: tuple = ()) -> list[tuple]:
    cur.execute(sql, params)
    return cur.fetchall()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meses", type=int, default=6,
                    help="ventana hacia atrás para medir (default 6)")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    desde_sql = f"concertacion >= (current_date - interval '{int(args.meses)} months')"
    # mismo criterio de volumen que la vista: sin cierres
    base = f"WHERE {desde_sql} AND COALESCE(es_cierre, false) = false"

    with get_pool().connection() as conn, conn.cursor() as cur:
        print(f"── 1. universo (últimos {args.meses} meses, sin cierres) ──")
        tot = _q(cur, f"SELECT count(*), coalesce(sum(bruto),0), "
                      f"count(DISTINCT instrumento) FROM operaciones.operaciones {base}")[0]
        n_boletos, vol_total, n_instr = int(tot[0]), float(tot[1] or 0), int(tot[2])
        print(f"boletos: {n_boletos:,} · volumen: {vol_total:,.0f} · "
              f"instrumentos distintos: {n_instr:,}")
        if not n_boletos:
            print("sin operaciones en la ventana — probá con --meses más grande")
            return

        print(f"\nassets: {_q(cur, 'SELECT count(*) FROM portafolio.assets')[0][0]:,} filas · "
              f"con cartera: "
              f"{_q(cur, 'SELECT count(*) FROM portafolio.assets WHERE cartera IS NOT NULL')[0][0]:,}")

        print("\n── 2. cobertura de cada candidato de JOIN (por VOLUMEN) ──")
        print(f"{'candidato':<34} {'% volumen':>10} {'% boletos':>10} {'instr. ok':>10}")
        print("-" * 68)
        mejor = (None, -1.0)
        for etiqueta, expr_ops, expr_ass in (
            ("assets.unidad (exacto)", "o.instrumento", "a.unidad"),
            ("assets.unidad (normalizado)", _NORM.format("o.instrumento"), _NORM.format("a.unidad")),
            ("assets.ticker (exacto)", "o.instrumento", "a.ticker"),
            ("assets.ticker (normalizado)", _NORM.format("o.instrumento"), _NORM.format("a.ticker")),
            ("assets.instrumento (exacto)", "o.instrumento", "a.instrumento"),
            ("assets.instrumento (normaliz.)", _NORM.format("o.instrumento"),
             _NORM.format("a.instrumento")),
        ):
            fila = _q(cur, f"""
                SELECT coalesce(sum(o.bruto) FILTER (WHERE a.cartera IS NOT NULL), 0),
                       count(*) FILTER (WHERE a.cartera IS NOT NULL),
                       count(DISTINCT o.instrumento) FILTER (WHERE a.cartera IS NOT NULL)
                FROM operaciones.operaciones o
                LEFT JOIN portafolio.assets a ON {expr_ass} = {expr_ops}
                {base.replace('WHERE', 'WHERE')}
            """)[0]
            vol_ok, bol_ok, instr_ok = float(fila[0] or 0), int(fila[1]), int(fila[2])
            pct_vol = 100 * vol_ok / vol_total if vol_total else 0
            pct_bol = 100 * bol_ok / n_boletos if n_boletos else 0
            print(f"{etiqueta:<34} {pct_vol:>9.1f}% {pct_bol:>9.1f}% {instr_ok:>10,}")
            if pct_vol > mejor[1]:
                mejor = ((etiqueta, expr_ops, expr_ass), pct_vol)

        (etiqueta, expr_ops, expr_ass), pct = mejor
        print(f"\n→ MEJOR: {etiqueta} ({pct:.1f}% del volumen)")

        print(f"\n── 3. top {args.top} instrumentos SIN cartera con ese join ──")
        for instr, vol, n in _q(cur, f"""
            SELECT o.instrumento, sum(o.bruto) AS vol, count(*) AS n
            FROM operaciones.operaciones o
            LEFT JOIN portafolio.assets a ON {expr_ass} = {expr_ops}
            {base} AND a.cartera IS NULL
            GROUP BY o.instrumento ORDER BY vol DESC NULLS LAST LIMIT {int(args.top)}
        """):
            pct_i = 100 * float(vol or 0) / vol_total if vol_total else 0
            print(f"  {str(instr or '(sin instrumento)')[:52]:<54} "
                  f"{float(vol or 0):>18,.0f}  {pct_i:>5.1f}%  ({n:,} bol.)")

        print("\n── 4. carteras que tendría el filtro ──")
        for cartera, vol, n in _q(cur, f"""
            SELECT a.cartera, sum(o.bruto) AS vol, count(*) AS n
            FROM operaciones.operaciones o
            JOIN portafolio.assets a ON {expr_ass} = {expr_ops}
            {base} AND a.cartera IS NOT NULL
            GROUP BY a.cartera ORDER BY vol DESC NULLS LAST
        """):
            pct_c = 100 * float(vol or 0) / vol_total if vol_total else 0
            print(f"  {str(cartera)[:28]:<30} {float(vol or 0):>18,.0f}  {pct_c:>5.1f}%  ({n:,} bol.)")

    print("\nCon esto se decide el join y se codea el filtro CARTERA en Operaciones.")


if __name__ == "__main__":
    main()
