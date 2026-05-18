"""smoke_rv_motor.py — verifica el motor de correlación de la Mesa de Estrategia.

Corre get_correlation_matrix sobre un set chico y sobre el universo entero e
imprime un resumen. Solo lectura. Sirve para confirmar el Paso 0 del feature
de docs/wip_mesa_estrategia_rv.md.

Uso:  python -m scripts.smoke_rv_motor
"""
from __future__ import annotations

from api.services.rv_motor import get_correlation_matrix, get_trade_analysis


def _resumen(r: dict) -> None:
    print(f"  incluidos={len(r['tickers'])}  excluidos={len(r['excluidos'])}  "
          f"n_obs={r['n_obs']}  rango={r['fecha_desde']}..{r['fecha_hasta']}")
    if r["excluidos"]:
        print(f"  excluidos: {r['excluidos']}")


def main() -> None:
    print("== set chico (NVDA, AMD, KO, GLD) — ventana 120 ==")
    chico = get_correlation_matrix(
        tickers=("NVDA", "AMD", "KO", "GLD"), ventana_dias=120,
    )
    _resumen(chico)
    for tk, fila in zip(chico["tickers"], chico["matriz"]):
        celdas = "  ".join(f"{c:+.2f}" if c is not None else "  na" for c in fila)
        print(f"  {tk:6} {celdas}")

    print("\n== universo entero — ventana 252 ==")
    full = get_correlation_matrix()
    _resumen(full)
    n = len(full["matriz"])
    print(f"  matriz {n}x{n}")

    print("\n== trade-analysis (IBIT, 1.000.000 USD, long) ==")
    ta = get_trade_analysis(ticker="IBIT", monto=1_000_000, direccion="long")
    c = ta["caracterizacion"]
    print(f"  last={c['last']}  vol60={c['vol_anual']['d60']}  "
          f"beta_qqq={c['beta']['qqq']}")
    print(f"  VaR 1d 95%: {c['var_1d_95']}  ({c['var_1d_95_pct']}%)  "
          f"peor mes 1σ: {c['peor_mes_1sigma']}")
    print(f"  hedge beta: {ta['hedge_beta']}")
    print("  top 5 hedge-finder (por |correlación|):")
    for h in ta["hedge_finder"][:5]:
        print(f"    {h['ticker']:6} corr={h['correlacion']:+.2f}  "
              f"{h['accion']:5}  ratio={h['hedge_ratio']}  "
              f"reduc_vol={h['reduccion_vol_pct']}%")


if __name__ == "__main__":
    main()
