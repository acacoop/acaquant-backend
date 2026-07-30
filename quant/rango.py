"""rango.py — carácter del rango de un papel: ATR diario y Efficiency Ratio.

Funciones PURAS (sin I/O, sin cache, sin FastAPI). Dos métricas de CONTEXTO
—no señales— para decidir si vale la pena operar niveles:

- ATR (Average True Range, 20 ruedas): rango típico DIARIO del papel en ARS.
  True range de una rueda = max(high-low, |high-close_prev|, |low-close_prev|)
  → captura también el gap de apertura contra el cierre previo. El ATR es el
  promedio de los últimos N true ranges: "cuánta nafta quema el papel en un
  día normal". Se calcula UNA vez por rueda, sobre OHLC diario.

- Efficiency Ratio (Kaufman): sobre barras de 1 minuto, mide qué tan LIMPIO es
  el movimiento = |precio_fin - precio_ini| / Σ|Δ de cada barra|. Cerca de 1 →
  tendencia derecha (los niveles funcionan); cerca de 0 → choppy/serrucho (los
  niveles no aguantan → no operar). Se mira en dos ventanas: últimas 30 barras
  (régimen AHORA) y desde la apertura (carácter del día).
"""
from __future__ import annotations

from collections.abc import Sequence


def true_range(high: float, low: float, close_prev: float) -> float:
    """True range de una rueda: el mayor entre el rango del día, el gap alcista
    contra el cierre previo y el gap bajista contra el cierre previo."""
    return max(high - low, abs(high - close_prev), abs(low - close_prev))


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    periodo: int = 20,
) -> float | None:
    """ATR sobre OHLC diario alineado y ASCENDENTE por fecha.

    Args:
        highs / lows / closes: listas alineadas (misma longitud, orden
            cronológico: el último elemento es la rueda más reciente).
        periodo: cuántos true ranges promedia (default 20 ruedas).

    Returns:
        El ATR en las MISMAS unidades que los precios (ARS), o None si no hay
        historia suficiente: N true ranges necesitan N+1 cierres, así que hacen
        falta `periodo + 1` ruedas.
    """
    n = min(len(highs), len(lows), len(closes))
    if n < periodo + 1:
        return None
    trs = [true_range(highs[i], lows[i], closes[i - 1]) for i in range(1, n)]
    ventana = trs[-periodo:]
    return sum(ventana) / len(ventana)


def efficiency_ratio(precios: Sequence[float]) -> float | None:
    """Kaufman Efficiency Ratio sobre una serie de cierres (1-min o cualquier
    grano, ascendente): |último - primero| / Σ|cambios de barra a barra|.

    Rango [0, 1]: ~1 → movimiento direccional limpio; ~0 → choppy. None si hay
    <2 puntos válidos o el papel no se movió (Σ|cambios| = 0)."""
    serie = [p for p in precios if p is not None and p > 0]
    if len(serie) < 2:
        return None
    direccion = abs(serie[-1] - serie[0])
    volatilidad = sum(abs(serie[i] - serie[i - 1]) for i in range(1, len(serie)))
    if volatilidad == 0:
        return None
    return direccion / volatilidad


def efficiency_ratio_ventanas(
    closes: Sequence[float], ventana_reciente: int = 30
) -> dict[str, float | None]:
    """Las dos ventanas del ER intradía sobre la serie de cierres de 1 minuto:

    - `er_dia`:     desde la apertura (carácter del día completo).
    - `er_reciente`: últimas `ventana_reciente` barras (régimen AHORA).

    Cada valor puede ser None si no hay datos suficientes en esa ventana."""
    return {
        "er_dia": efficiency_ratio(closes),
        "er_reciente": efficiency_ratio(list(closes)[-ventana_reciente:]),
    }
