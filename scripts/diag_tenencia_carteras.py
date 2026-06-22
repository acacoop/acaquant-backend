"""diag_tenencia_carteras.py — distribución por CARTERA de las cuentas propias (100/255/256).

Read-only. Antes de sumar la vista 'Cartera ARS' a Tenencia Valorizada: ver qué carteras
tienen realmente esas cuentas en la última tenencia AuM, para definir bien el filtro
'todo lo que no sea HD' y confirmar que no está vacío.

    python -m scripts.diag_tenencia_carteras
"""
from __future__ import annotations

from core.postgres import get_pool

CUENTAS = ["100", "255", "256"]


def main() -> int:
    with get_pool().connection() as cn, cn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        fecha = cur.fetchone()[0]
        print(f"Última tenencia AuM: {fecha} · cuentas {CUENTAS}\n")

        cur.execute(
            "SELECT COALESCE(a.cartera, '(sin cartera / sin asset)') AS cartera, "
            "       COUNT(DISTINCT t.unidad) AS unidades, "
            "       ROUND(SUM(t.valuacion)::numeric, 0) AS valuacion "
            "FROM portafolio.tenencia t "
            "LEFT JOIN portafolio.assets a ON a.unidad = t.unidad "
            "WHERE t.fecha = %s AND t.aum = 'si' AND t.id_cuenta = ANY(%s) "
            "GROUP BY 1 ORDER BY 3 DESC NULLS LAST", (fecha, CUENTAS))
        rows = cur.fetchall()

    print(f"{'CARTERA':<28} {'#unid':>6} {'VALUACIÓN (ARS)':>20}")
    print("─" * 56)
    tot_hd = tot_no = 0.0
    for cartera, nun, val in rows:
        val = float(val or 0.0)
        print(f"{cartera:<28} {nun:>6} {val:>20,.0f}")
        if cartera == "HD":
            tot_hd += val
        else:
            tot_no += val
    print("─" * 56)
    print(f"{'TOTAL HD (= Cartera USD)':<28} {'':>6} {tot_hd:>20,.0f}")
    print(f"{'TOTAL no-HD (= Cartera ARS)':<28} {'':>6} {tot_no:>20,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
