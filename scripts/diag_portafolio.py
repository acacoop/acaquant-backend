"""scripts/diag_portafolio.py — leer portafolio.tenencia (validar el backfill).

Read-only. Muestra lo que quedó en el schema SQL `portafolio` para una cuenta:
detalle por (fecha, especie) + total por fecha, y un resumen del backfill_log.

Uso:
    python -m scripts.diag_portafolio --cuenta 255
    python -m scripts.diag_portafolio --cuenta 255 --fecha 2026-06-02
    python -m scripts.diag_portafolio --cuenta 255 --ticker GD35   # solo una especie
"""
from __future__ import annotations

import sys

from psycopg.rows import dict_row

from core.postgres import get_pool


def _opt(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _q(sql, params):
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def main() -> int:
    cuenta = _opt("--cuenta", "255")
    fecha = _opt("--fecha")
    ticker = _opt("--ticker")

    conds = ["id_cuenta = %(c)s"]
    p = {"c": cuenta}
    if fecha:
        conds.append("fecha = %(f)s")
        p["f"] = fecha
    if ticker:
        conds.append("(ticker ILIKE %(t)s OR unidad ILIKE %(t)s)")
        p["t"] = f"%{ticker}%"
    where = " AND ".join(conds)

    rows = _q(f"SELECT fecha, unidad, ticker, cartera, cantidad, precio, valuacion, moneda "
              f"FROM portafolio.tenencia WHERE {where} "
              f"ORDER BY fecha, valuacion DESC", p)
    print(f"=== portafolio.tenencia · cuenta {cuenta}"
          f"{' · fecha ' + fecha if fecha else ''}{' · ' + ticker if ticker else ''} ===")
    print(f"  filas: {len(rows)}")
    if rows:
        print(f"  {'FECHA':<12} {'TICKER/UNIDAD':<34} {'CANTIDAD':>15} {'PRECIO':>12} {'VALUACION':>16}")
        for r in rows[:80]:
            etq = (r["ticker"] or r["unidad"] or "")[:34]
            print(f"  {r['fecha']!s:<12} {etq:<34} {float(r['cantidad'] or 0):>15,.2f} "
                  f"{float(r['precio'] or 0):>12,.2f} {float(r['valuacion'] or 0):>16,.2f}")
        if len(rows) > 80:
            print(f"  … +{len(rows) - 80} filas")

    # total por fecha
    tot = _q("SELECT fecha, count(*) AS n, sum(valuacion) AS val "
             "FROM portafolio.tenencia WHERE id_cuenta = %(c)s GROUP BY fecha ORDER BY fecha",
             {"c": cuenta})
    print(f"\n  --- total por fecha (cuenta {cuenta}) ---")
    for r in tot:
        print(f"  {r['fecha']!s:<12} {r['n']:>4} especies   valuación: {float(r['val'] or 0):>18,.2f}")

    # backfill log
    log = _q("SELECT fecha, status, count(*) AS n FROM portafolio.backfill_log "
             "GROUP BY fecha, status ORDER BY fecha, status", {})
    if log:
        print("\n  --- backfill_log (qué se procesó) ---")
        for r in log:
            print(f"  {r['fecha']!s:<12} {r['status']:<14} {r['n']:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
