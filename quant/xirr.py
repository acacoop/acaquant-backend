"""xirr.py — TIR.NO.PER de Excel (XIRR / IRR para flujos en fechas
irregulares).

Función pura. Sin Mongo, sin FastAPI, sin dependencias externas
(solo stdlib). Vive en `quant/` porque es cálculo de propósito general.

Modelo:
    Dado un set de cashflows (fecha, monto), devuelve la TEA (tasa
    efectiva anual) r tal que NPV(r) = 0:

        NPV(r) = Σ flow_i / (1 + r) ^ (días_i / 365) = 0

    donde días_i = (fecha_i - fecha_min).days

Convención de signos (consumidor decide; XIRR no la impone):
    - Una opción: outflows del cliente (-) / inflows (+). Es la
      convención canónica de Excel.
    - La opuesta también funciona — XIRR es invariante al swap global
      de signos. Lo único que importa es que NPV(r) cambie de signo
      en algún r realista.

Reglas:
    - Necesita al menos 2 puntos.
    - Necesita que haya al menos un flujo positivo y uno negativo
      (sin eso NPV no tiene raíz).
    - Devuelve `None` si no converge — el caller decide qué hacer.

Algoritmo: Newton-Raphson con guess inicial = 0.10, fallback a bisección
si Newton diverge / oscila. Bisección busca en [-0.9999, 10] (TEA entre
-99.99% y +1000%).

Validado contra Excel TIR.NO.PER con casos canónicos (ver tests).
"""
from __future__ import annotations

from datetime import date
from typing import Sequence

DAYS_PER_YEAR = 365.0
_MAX_ITER_NEWTON = 100
_TOL_NPV = 1e-7
_TOL_RATE = 1e-9
_MAX_ITER_BISECT = 200
_RATE_MIN = -0.9999
_RATE_MAX = 10.0


def _npv(rate: float, flows: list[tuple[float, float]]) -> float:
    """NPV dado tuples (años_desde_t0, monto)."""
    total = 0.0
    for years, amount in flows:
        total += amount / (1.0 + rate) ** years
    return total


def _dnpv_drate(rate: float, flows: list[tuple[float, float]]) -> float:
    """Derivada del NPV respecto a rate."""
    total = 0.0
    for years, amount in flows:
        if years == 0:
            continue
        total += -years * amount / (1.0 + rate) ** (years + 1)
    return total


def _bisect(flows: list[tuple[float, float]]) -> float | None:
    lo, hi = _RATE_MIN, _RATE_MAX
    npv_lo = _npv(lo, flows)
    npv_hi = _npv(hi, flows)
    if npv_lo * npv_hi > 0:
        return None
    for _ in range(_MAX_ITER_BISECT):
        mid = (lo + hi) / 2
        npv_mid = _npv(mid, flows)
        if abs(npv_mid) < _TOL_NPV:
            return mid
        if npv_lo * npv_mid < 0:
            hi = mid
            npv_hi = npv_mid
        else:
            lo = mid
            npv_lo = npv_mid
        if hi - lo < _TOL_RATE:
            return (lo + hi) / 2
    return (lo + hi) / 2


def xirr(cashflows: Sequence[tuple[date, float]], guess: float = 0.10) -> float | None:
    """Resuelve la TEA implícita en un set de cashflows con fechas.

    Args:
        cashflows: lista de (fecha, monto). Necesita al menos 2 entradas
            y al menos un monto positivo y uno negativo.
        guess: estimación inicial para Newton-Raphson. Default 10%.

    Returns:
        TEA (tasa efectiva anual) como decimal — 0.26 significa 26%.
        `None` si no converge o los inputs son degenerados.
    """
    if len(cashflows) < 2:
        return None

    nonzero = [(d, a) for d, a in cashflows if a != 0]
    if len(nonzero) < 2:
        return None

    montos = [a for _, a in nonzero]
    if min(montos) >= 0 or max(montos) <= 0:
        # Todos del mismo signo → NPV no tiene raíz.
        return None

    # Normalizamos: convertimos cada (fecha, monto) a (años_desde_t0, monto).
    t0 = min(d for d, _ in nonzero)
    flows: list[tuple[float, float]] = [
        ((d - t0).days / DAYS_PER_YEAR, a) for d, a in nonzero
    ]

    # Newton-Raphson.
    rate = guess
    for _ in range(_MAX_ITER_NEWTON):
        f = _npv(rate, flows)
        if abs(f) < _TOL_NPV:
            return rate
        df = _dnpv_drate(rate, flows)
        if df == 0:
            break
        new_rate = rate - f / df
        # Si Newton intenta saltar fuera del rango razonable, abortamos.
        if new_rate <= _RATE_MIN or new_rate > _RATE_MAX:
            break
        if abs(new_rate - rate) < _TOL_RATE:
            return new_rate
        rate = new_rate

    # Fallback: bisección.
    return _bisect(flows)
