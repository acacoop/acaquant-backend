"""diag_pivot_points.py — validación visual de los 4 timeframes.

Para NVDA + AMD, imprime los rangos detectados (primer / último día) y
los 7 niveles calculados de cada timeframe (diario, semanal, mensual,
anual). Sirve para confirmar que la lógica de "período previo" está bien
antes de mostrar nada en la UI.

Uso:
    python -m scripts.diag_pivot_points              # NVDA + AMD
    python -m scripts.diag_pivot_points NVDA SPY GGAL
"""
from __future__ import annotations

import sys

from quant.pivot_points import obtener_4_timeframes


def _fmt(v):
    if v is None:
        return "∅"
    if isinstance(v, float):
        return f"{v:>10.2f}"
    return str(v)


def _print_frame(name: str, frame: dict | None) -> None:
    print(f"\n  [{name.upper()}]")
    if frame is None:
        print(f"    ∅ Sin data (probable: ticker arrancó después del rango)")
        return
    fd = frame["fecha_desde"].strftime("%Y-%m-%d") if frame.get("fecha_desde") else "—"
    fh = frame["fecha_hasta"].strftime("%Y-%m-%d") if frame.get("fecha_hasta") else "—"
    print(f"    Rango:   {fd} → {fh}  ({frame['n_velas']} velas)")
    print(f"    H/L/C:   H={frame['h']:.2f}  L={frame['l']:.2f}  C={frame['c']:.2f}")
    lv = frame["levels"]
    print(f"    Levels:  R3={_fmt(lv['r3'])}  R2={_fmt(lv['r2'])}  R1={_fmt(lv['r1'])}")
    print(f"             PP={_fmt(lv['pp'])}")
    print(f"             S1={_fmt(lv['s1'])}  S2={_fmt(lv['s2'])}  S3={_fmt(lv['s3'])}")


def run(tickers: list[str]) -> None:
    print("=" * 100)
    print(f"DIAG pivot points — {tickers}")
    print("=" * 100)

    for ticker in tickers:
        print(f"\n{'═' * 100}")
        print(f"  {ticker}")
        print("═" * 100)

        res = obtener_4_timeframes(ticker)
        last       = res.get("last")
        last_fecha = res.get("last_fecha")

        last_str = f"{last:.2f}" if last is not None else "∅"
        fecha_str = last_fecha.strftime("%Y-%m-%d") if last_fecha else "—"
        print(f"\n  Último close: {last_str} USD ({fecha_str})")

        for nombre in ("diario", "semanal", "mensual", "anual"):
            _print_frame(nombre, res["frames"][nombre])


if __name__ == "__main__":
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["NVDA", "AMD"]
    run([t.upper() for t in tickers])
