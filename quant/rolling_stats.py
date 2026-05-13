"""rolling_stats.py — beta / alpha / correlación / vol realizada
sobre series de precios.

Funciones puras (sin Mongo, sin cache, sin FastAPI). Operan sobre listas
de floats (retornos diarios) en memoria. Lectura de la serie histórica
desde Mongo es responsabilidad del caller.

Convención: retornos ARITMÉTICOS (r_t = price_t / price_{t-1} - 1) —
es lo que la mesa interpreta naturalmente. Si en algún momento se
necesita log-returns, se agrega una variante.

Anualización: factor √252 (días hábiles US/AR estándar).
"""
from __future__ import annotations

import math
import statistics
from typing import Sequence


# Días hábiles por año — base para anualizar vol y alpha.
TRADING_DAYS_PER_YEAR = 252


def returns_from_prices(prices: Sequence[float]) -> list[float]:
    """Aritméticos: r_t = p_t / p_{t-1} − 1. Devuelve N-1 valores."""
    if len(prices) < 2:
        return []
    out: list[float] = []
    for i in range(1, len(prices)):
        prev = prices[i - 1]
        curr = prices[i]
        if prev is None or curr is None or prev <= 0:
            continue
        out.append(curr / prev - 1)
    return out


def realized_vol(returns: Sequence[float]) -> float | None:
    """Volatilidad anualizada = stdev(returns) × √252.

    Devuelve None si la serie es muy corta (< 2 puntos).
    """
    rets = [r for r in returns if r is not None]
    if len(rets) < 2:
        return None
    return statistics.stdev(rets) * math.sqrt(TRADING_DAYS_PER_YEAR)


def correlation(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Pearson correlation. Trunca al mínimo común.

    Devuelve None si la serie efectiva es corta o sin varianza.
    """
    n = min(len(a), len(b))
    if n < 2:
        return None
    aa, bb = list(a[-n:]), list(b[-n:])
    # Limpiar None pares (si alguno es None, descartamos la fila).
    pairs = [(x, y) for x, y in zip(aa, bb) if x is not None and y is not None]
    if len(pairs) < 2:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return None


def beta_alpha(
    asset_returns: Sequence[float],
    bench_returns: Sequence[float],
    annualize_alpha: bool = True,
) -> dict:
    """OLS lineal: asset = alpha + beta × bench + eps.

    beta  = cov(asset, bench) / var(bench)
    alpha = mean(asset) − beta × mean(bench)
            (multiplicado por 252 si annualize_alpha=True)

    Returns:
        {beta, alpha, r2} con None si la serie es muy corta o degenerada.
    """
    n = min(len(asset_returns), len(bench_returns))
    if n < 5:
        return {"beta": None, "alpha": None, "r2": None}

    a, b = list(asset_returns[-n:]), list(bench_returns[-n:])
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if len(pairs) < 5:
        return {"beta": None, "alpha": None, "r2": None}
    asset = [p[0] for p in pairs]
    bench = [p[1] for p in pairs]

    try:
        var_b = statistics.variance(bench)
        if var_b <= 0:
            return {"beta": None, "alpha": None, "r2": None}
        cov_ab = statistics.covariance(asset, bench)
    except statistics.StatisticsError:
        return {"beta": None, "alpha": None, "r2": None}

    beta = cov_ab / var_b
    mean_a = statistics.fmean(asset)
    mean_b = statistics.fmean(bench)
    alpha_diario = mean_a - beta * mean_b
    alpha = alpha_diario * TRADING_DAYS_PER_YEAR if annualize_alpha else alpha_diario

    # R² = corr²
    corr = correlation(asset, bench)
    r2 = corr * corr if corr is not None else None

    return {"beta": beta, "alpha": alpha, "r2": r2}
