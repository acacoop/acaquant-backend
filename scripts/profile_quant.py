"""profile_quant.py — Microbenchmark del cálculo PURO de quant/.

Objetivo: responder con números (no intuición) si el cómputo de quant/ es un
cuello de botella que justifique vectorizar / numba / C++ / Rust. Mide cada
función pura con `timeit` sobre datasets del tamaño REAL que maneja la mesa, y
contrasta el costo contra el budget típico de un request (decenas de ms) y
contra una query Mongo (decenas-cientos de ms).

NO toca Mongo ni red: usa datos sintéticos del mismo orden de magnitud que los
reales (cadena de opciones, ~40-250 ruedas de precios, ~10 puntos de curva,
~12-50 cashflows). Las funciones que leen Mongo (calcular_hv_40_ruedas,
pivot_points.obtener_*) quedan fuera a propósito — su costo es I/O, no CPU.

Uso:
    python -m scripts.profile_quant
"""
from __future__ import annotations

import random
import timeit
from datetime import date, timedelta

from quant import black_scholes as bs
from quant import curve_fit, pivot_points, rolling_stats, stats, xirr

random.seed(42)

# Presupuestos de referencia para poner los números en contexto.
REQUEST_BUDGET_MS = 50.0   # request "rápido" objetivo
MONGO_QUERY_MS = 80.0      # una query/agg típica a Atlas (orden de magnitud)


def _bench(fn, *, number: int) -> float:
    """Devuelve microsegundos por llamada (mediana de 7 corridas)."""
    runs = timeit.repeat(fn, number=number, repeat=7)
    best = min(runs) / number
    return best * 1e6  # → microsegundos


def _row(name: str, us: float, n_real: int) -> dict:
    """Arma una fila con μs/call y cuánto cuesta el batch real completo."""
    batch_ms = us * n_real / 1000.0
    return {"name": name, "us": us, "n_real": n_real, "batch_ms": batch_ms}


# ──────────────────────────────────────────────────────────────────────────
# Datasets sintéticos del tamaño real
# ──────────────────────────────────────────────────────────────────────────
PRICES_250 = [100.0]
for _ in range(249):
    PRICES_250.append(PRICES_250[-1] * (1 + random.gauss(0, 0.02)))
PRICES_40 = PRICES_250[-40:]

RETS_250 = rolling_stats.returns_from_prices(PRICES_250)
RETS_BENCH = rolling_stats.returns_from_prices(
    [100.0 * (1 + random.gauss(0, 0.015)) for _ in range(250)]
)

# Cadena de opciones: ~120 strikes activos en un vto típico de GGAL/YPF.
N_OPCIONES = 120
CHAIN = [
    {"S": 100.0, "K": 80.0 + i, "T": 0.08, "r": 0.0, "mkt": max(0.5, 100.0 - (80.0 + i))}
    for i in range(N_OPCIONES)
]

# Curva tasa fija: ~10 puntos (duration, TEA).
DURS = [0.1 * i + 0.2 for i in range(10)]
TEAS = [0.30 + 0.02 * d - 0.003 * d * d + random.gauss(0, 0.005) for d in DURS]

# XIRR: flujo de un bono con ~24 cupones semestrales + nominal.
CASHFLOWS = [(date(2024, 1, 1), -1000.0)]
for i in range(1, 25):
    CASHFLOWS.append((date(2024, 1, 1) + timedelta(days=180 * i), 30.0))
CASHFLOWS.append((date(2024, 1, 1) + timedelta(days=180 * 25), 1000.0))

# Serie genérica para stats (ej. histórico de un spread / TEA).
SERIE = [random.gauss(5.0, 1.2) for _ in range(252)]


# ──────────────────────────────────────────────────────────────────────────
# Benchmarks
# ──────────────────────────────────────────────────────────────────────────
def run() -> list[dict]:
    rows: list[dict] = []

    # --- Black-Scholes (escalar; se llama 1x por opción de la cadena) ---
    rows.append(_row(
        "bs_price",
        _bench(lambda: bs.bs_price(100, 95, 0.08, 0.0, 0.45, "CALL"), number=20000),
        N_OPCIONES,
    ))
    rows.append(_row(
        "bs_delta",
        _bench(lambda: bs.bs_delta(100, 95, 0.08, 0.0, 0.45, "CALL"), number=20000),
        N_OPCIONES,
    ))
    rows.append(_row(
        "bs_gamma",
        _bench(lambda: bs.bs_gamma(100, 95, 0.08, 0.0, 0.45), number=20000),
        N_OPCIONES,
    ))
    rows.append(_row(
        "bs_vega",
        _bench(lambda: bs.bs_vega(100, 95, 0.08, 0.0, 0.45), number=20000),
        N_OPCIONES,
    ))
    rows.append(_row(
        "bs_theta",
        _bench(lambda: bs.bs_theta(100, 95, 0.08, 0.0, 0.45, "CALL"), number=20000),
        N_OPCIONES,
    ))
    # find_iv: loop Newton-Raphson de hasta 20 iter — el más caro de la familia.
    rows.append(_row(
        "find_iv (20-iter Newton)",
        _bench(lambda: bs.find_iv(8.0, 100, 95, 0.08, 0.0, "CALL"), number=5000),
        N_OPCIONES,
    ))

    # --- rolling_stats (sobre ~250 ruedas) ---
    rows.append(_row(
        "returns_from_prices (n=250)",
        _bench(lambda: rolling_stats.returns_from_prices(PRICES_250), number=5000),
        1,
    ))
    rows.append(_row(
        "realized_vol (n=249)",
        _bench(lambda: rolling_stats.realized_vol(RETS_250), number=5000),
        1,
    ))
    rows.append(_row(
        "zscore_last (n=249)",
        _bench(lambda: rolling_stats.zscore_last(RETS_250), number=5000),
        1,
    ))
    rows.append(_row(
        "correlation (n=249)",
        _bench(lambda: rolling_stats.correlation(RETS_250, RETS_BENCH), number=5000),
        1,
    ))
    rows.append(_row(
        "beta_alpha (n=249)",
        _bench(lambda: rolling_stats.beta_alpha(RETS_250, RETS_BENCH), number=2000),
        1,
    ))

    # --- stats (paquete completo sobre 252 obs) ---
    rows.append(_row(
        "compute_stats (n=252)",
        _bench(lambda: stats.compute_stats(SERIE, 5.5), number=5000),
        1,
    ))
    rows.append(_row(
        "classify_level (n=252)",
        _bench(lambda: stats.classify_level(SERIE, 5.5), number=5000),
        1,
    ))

    # --- curve_fit (10 puntos) ---
    rows.append(_row(
        "fit_quadratic (n=10)",
        _bench(lambda: curve_fit.fit_quadratic(DURS, TEAS), number=10000),
        1,
    ))

    # --- xirr (26 flujos, Newton + brent) ---
    rows.append(_row(
        "xirr (26 flujos)",
        _bench(lambda: xirr.xirr(CASHFLOWS), number=2000),
        1,
    ))

    # --- pivot_points.calcular (escalar puro) ---
    rows.append(_row(
        "pivot calcular",
        _bench(lambda: pivot_points.calcular(110.0, 95.0, 102.0), number=50000),
        4,  # 4 timeframes
    ))

    return rows


def main() -> None:
    rows = run()

    print("\n" + "=" * 78)
    print("MICROBENCHMARK quant/ — cálculo puro, datasets de tamaño real")
    print("=" * 78)
    print(f"{'función':<32}{'μs/call':>12}{'n batch':>10}{'batch ms':>14}")
    print("-" * 78)
    for r in rows:
        print(f"{r['name']:<32}{r['us']:>12.3f}{r['n_real']:>10}{r['batch_ms']:>14.4f}")
    print("-" * 78)

    total_batch_ms = sum(r["batch_ms"] for r in rows)
    print(f"{'TOTAL (un pase de todo)':<32}{'':>12}{'':>10}{total_batch_ms:>14.4f} ms")
    print("=" * 78)

    print("\nContexto:")
    print(f"  • Budget de un request 'rápido':        ~{REQUEST_BUDGET_MS:.0f} ms")
    print(f"  • Una query/agg típica a Atlas:         ~{MONGO_QUERY_MS:.0f} ms")
    print(f"  • Todo el cómputo quant junto:          ~{total_batch_ms:.4f} ms")
    ratio = MONGO_QUERY_MS / total_batch_ms if total_batch_ms else float("inf")
    print(f"  • Una sola query Mongo cuesta ~{ratio:,.0f}x todo el cálculo puro.")
    print("\nVeredicto: si batch ms << query Mongo, el sistema es I/O-bound y")
    print("reescribir en C++/Rust no mueve la aguja. Atacar shape de queries +")
    print("índices + caching es donde están los ms reales.\n")


if __name__ == "__main__":
    main()
