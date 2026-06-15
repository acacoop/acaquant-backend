"""scripts/compare_aum_tenencia.py — READ-ONLY. Compara la fuente vieja (tabla SQL
`aum`, espejo de Mongo) vs la nueva (`portafolio.tenencia` aum='si') para el último
snapshot. Sirve de GATE antes de matar Mongo AuM: pnl_sql/comercial_sql ahora leen
tenencia → este diag muestra que el total NO se mueve raro (la diferencia esperada
es el fix de MONEDAS ÷100 + correcciones de fecha).

    python -m scripts.compare_aum_tenencia            # totales + top deltas por cuenta
    python -m scripts.compare_aum_tenencia --all      # todas las cuentas con delta

No escribe nada.
"""
from __future__ import annotations

import sys

from core.postgres import get_pool


def _total_por_cuenta(cur, sql, params=()) -> dict[str, float]:
    cur.execute(sql, params)
    return {str(r[0]): float(r[1] or 0) for r in cur.fetchall()}


def main() -> None:
    mostrar_todo = "--all" in sys.argv
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha_snapshot) FROM aum")
        f_aum = cur.fetchone()[0]
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        f_ten = cur.fetchone()[0]
        print("\n=== AuM (tabla `aum`, Mongo) vs portafolio.tenencia (aum='si') ===")
        print(f"   último snapshot  aum={f_aum}   tenencia={f_ten}")

        aum = _total_por_cuenta(
            cur, "SELECT id_cuenta, SUM(valuacion) FROM aum WHERE fecha_snapshot = %s "
                 "GROUP BY id_cuenta", (f_aum,))
        ten = _total_por_cuenta(
            cur, "SELECT id_cuenta, SUM(valuacion) FROM portafolio.tenencia "
                 "WHERE fecha = %s AND aum = 'si' GROUP BY id_cuenta", (f_ten,))

    tot_aum, tot_ten = sum(aum.values()), sum(ten.values())
    print(f"\n   TOTAL aum      : {tot_aum:>20,.2f}")
    print(f"   TOTAL tenencia : {tot_ten:>20,.2f}")
    print(f"   delta          : {tot_ten - tot_aum:>20,.2f}  "
          f"({100 * (tot_ten - tot_aum) / tot_aum:+.2f}%)" if tot_aum else "")
    print(f"   cuentas: aum={len(aum)}  tenencia={len(ten)}  "
          f"solo_aum={len(set(aum) - set(ten))}  solo_ten={len(set(ten) - set(aum))}")

    deltas = []
    for idc in set(aum) | set(ten):
        d = ten.get(idc, 0) - aum.get(idc, 0)
        if abs(d) > 1:
            deltas.append((idc, aum.get(idc, 0), ten.get(idc, 0), d))
    deltas.sort(key=lambda x: -abs(x[3]))
    print(f"\n   cuentas con delta > $1: {len(deltas)}")
    print(f"   {'id_cuenta':<12}{'aum':>18}{'tenencia':>18}{'delta':>18}")
    for idc, a, t, d in (deltas if mostrar_todo else deltas[:25]):
        print(f"   {idc:<12}{a:>18,.0f}{t:>18,.0f}{d:>18,.0f}")
    if not mostrar_todo and len(deltas) > 25:
        print(f"   … (+{len(deltas) - 25} más, --all para verlas)")
    print()


if __name__ == "__main__":
    main()
