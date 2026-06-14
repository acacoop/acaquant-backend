"""scripts/diag_tea_805.py — valida TEA/TWR (USD) desde SQL para una cuenta.

Corre `valuacion_mensual_debug` con engine='sql' (cierres desde portafolio.tenencia,
dato corregido) y muestra, por mes, EXACTO lo que entra al XIRR en USD: valor de
inicio/cierre, MEP del cierre, depósitos, extracciones, flujo neto y el cashflow
completo (fecha · monto · tipo). Read-only.

Uso:
    python -m scripts.diag_tea_805              # cuenta 805, motor SQL
    python -m scripts.diag_tea_805 255          # otra cuenta
    python -m scripts.diag_tea_805 805 --mongo  # mismo desglose pero leyendo Mongo (comparar)
"""
from __future__ import annotations

import sys

from api.services.valuaciones import valuacion_mensual_debug


def _pct(x):
    return f"{x * 100:.2f}%" if x is not None else "—"


def _m(x):
    return f"{x:,.2f}" if isinstance(x, (int, float)) else "—"


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    id_cuenta = args[0] if args else "805"
    engine = "mongo" if "--mongo" in sys.argv else "sql"

    data = valuacion_mensual_debug(id_cuenta, engine=engine)
    meses = data.get("meses", [])
    print("=" * 80)
    print(f"TEA/TWR USD · cuenta {id_cuenta} · engine={engine} · {len(meses)} meses")
    print("=" * 80)
    for m in meses:
        print("-" * 80)
        print(f"{m['mes']}   {m.get('fecha_inicio')} → {m.get('fecha_cierre')}  "
              f"({m.get('dias_periodo')}d)   MEP cierre = {m.get('mep_cierre')}")
        print(f"  USD   inicio = {_m(m.get('valor_inicio_usd'))}   "
              f"cierre = {_m(m.get('valor_cierre_usd'))}")
        print(f"        depósitos = {_m(m.get('depositos_usd'))}   "
              f"extracciones = {_m(m.get('extracciones_usd'))}   "
              f"flujo neto = {_m(m.get('flujo_neto_usd'))}")
        print(f"        delta_real_usd = {_m(m.get('delta_real_usd'))}   "
              f"TEA_usd = {_pct(m.get('tea_mensual_usd'))}   "
              f"TWR_usd = {m.get('twr_base100_acum_usd')}")
        cf = m.get("cashflow_xirr_usd") or []
        if cf:
            print("        cashflow que entra al XIRR (USD):")
            for c in cf:
                print(f"          {c.get('fecha')}   {_m(c.get('monto'))}   {c.get('tipo')}")
        flj = m.get("flujos_individuales") or []
        if flj:
            print("        flujos del mes (orig → ARS, MEP):")
            for d in flj:
                print(f"          {d.get('fecha')}  {d.get('categoria')!s:<14} "
                      f"{d.get('moneda')} orig={_m(d.get('importe_original'))} "
                      f"mep={d.get('mep_aplicado')} ars={_m(d.get('importe_ars'))}")
    r = data.get("resumen", {})
    print("=" * 80)
    print(f"TWR final USD = {r.get('twr_final_usd')}   ganancia% USD = {r.get('ganancia_pct_usd')}")
    print("(read-only — no se escribió nada)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
