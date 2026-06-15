"""scripts/diag_pnl_valor.py — READ-ONLY. Localiza el ×100 en PnL Títulos.

Llama al motor SQL de PnL para una cuenta y muestra, por posición:
  - valor_aum   = valuacion del snapshot (portafolio.tenencia)
  - valor_live  = el que calcula el motor (normalizer: cartera → ÷100)
  - fuente      = live | cierre | aum
  - ratio live/aum (si ≈100 → el normalizer NO dividió; si ambos ya vienen ×100
    el problema es la valuacion del snapshot, no el motor)

Además cruza con la fila cruda de tenencia (cartera, tipo_titulo, precio, cantidad).

    python -m scripts.diag_pnl_valor 255       # id_cuenta
"""
from __future__ import annotations

import sys

from api.services import pnl_sql
from core.postgres import get_pool


def main() -> None:
    if len(sys.argv) < 2:
        print("uso: python -m scripts.diag_pnl_valor <id_cuenta>")
        return
    idc = sys.argv[1].strip()

    # 1) fila cruda de tenencia (último snapshot) por unidad
    crudo: dict[str, dict] = {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        f = cur.fetchone()[0]
        cur.execute(
            "SELECT unidad, cartera, tipo_titulo, precio, cantidad, valuacion "
            "FROM portafolio.tenencia WHERE fecha = %s AND aum = 'si' AND id_cuenta = %s", (f, idc))
        for u, cart, tt, prec, cant, val in cur.fetchall():
            crudo[u] = {"cartera": cart, "tt": tt, "precio": prec, "cant": cant, "val": val}

    print(f"\n=== PnL cuenta {idc} · snapshot {f} ===\n")
    res = pnl_sql.pnl_por_cuenta_sql(id_cuenta=idc)
    posiciones = res.get("posiciones") or res.get("rows") or []
    print(f"{'ticker':<14}{'fuente':<7}{'cartera':<10}{'precio':>10}"
          f"{'valor_aum':>16}{'valor_live':>16}{'ratio':>8}")
    for p in sorted(posiciones, key=lambda x: -(x.get("valor_actual_live") or 0))[:30]:
        u = p.get("unidad", "")
        c = crudo.get(u, {})
        va = p.get("valor_actual_aum") or 0
        vl = p.get("valor_actual_live") or 0
        ratio = (vl / va) if va else 0
        print(f"{(p.get('ticker') or '')[:13]:<14}{(p.get('valor_actual_source') or '')[:6]:<7}"
              f"{(c.get('cartera') or '(NULL)')[:9]:<10}{float(c.get('precio') or 0):>10.2f}"
              f"{va:>16,.0f}{vl:>16,.0f}{ratio:>8.1f}")
    print()


if __name__ == "__main__":
    main()
