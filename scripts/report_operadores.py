"""scripts/report_operadores.py — ranking de operadores por AuM, leyendo de Postgres. Fase C.

Demo de reportería SQL sobre el espejo de Supabase: AuM administrado y # de cuentas por
operador. En Mongo esto exige traer Comitentes (DB Clientes) y AuM (DB Valuaciones) a
Python y joinear a mano; en SQL es UNA query (ver api/services/reporting.py).

    python -m scripts.report_operadores
"""
from __future__ import annotations

from api.services.reporting import reporte_operadores


def main() -> int:
    r = reporte_operadores()
    print(f"AuM al último snapshot: {r['fecha']}   total = {r['aum_total']:,.0f}\n")
    print(f"{'OPERADOR':34}{'CUENTAS':>9}{'AUM':>18}{'%':>7}")
    print("-" * 68)
    attribuido = 0.0
    for row in r["ranking"]:
        aum = float(row["aum_total"] or 0)
        attribuido += aum
        pct = 100 * aum / r["aum_total"] if r["aum_total"] else 0
        nombre = (row["operador"] or row["operador_email"] or "—")[:34]
        print(f"{nombre:34}{row['n_cuentas']:>9}{aum:>18,.0f}{pct:>6.1f}%")
    print("-" * 68)
    sin_op = r["aum_total"] - attribuido
    print(f"{'(AuM sin operador asignado)':34}{'':>9}{sin_op:>18,.0f}"
          f"{(100 * sin_op / r['aum_total'] if r['aum_total'] else 0):>6.1f}%")
    print(f"{'TOTAL':34}{'':>9}{r['aum_total']:>18,.0f}{100.0:>6.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
