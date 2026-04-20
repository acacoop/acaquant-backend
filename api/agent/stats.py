"""Helpers estadísticos sobre series numéricas.

Funciones puras (sin Mongo, sin cache) usadas por:
- `obtener_serie_macro` / `clasificar_nivel` (api/services/macro.py)
- Tools de análisis que necesitan contextualizar un valor vs historia.

Implementa el framework de "benchmarks dinámicos" acordado con el user:
en vez de evaluar valores absolutos (que cambian cada día), clasificamos
relativos a la propia historia — percentil, z-score, tendencia.
"""
from __future__ import annotations

import statistics
from typing import Any

# Umbrales de clasificación (percentiles 0-100)
_PCT_MINIMO_MAX = 10
_PCT_BAJO_MAX = 30
_PCT_ALTO_MIN = 70
_PCT_MAXIMO_MIN = 90

# Umbrales de tendencia: cambio pct entre primer tercio y último tercio de la serie
_TENDENCIA_LATERAL = 0.02   # ±2% → lateral
_TENDENCIA_FUERTE = 0.10    # ≥10% → alcista/bajista

# Mínimo de puntos para clasificar con confianza
_MIN_PUNTOS = 5


def percentile_of(values: list[float], target: float) -> float | None:
    """Devuelve el percentil (0-100) de `target` en la distribución `values`.

    Calculado como: (#valores < target + 0.5 × #valores == target) / n × 100.
    Devuelve None si values está vacío.
    """
    if not values:
        return None
    below = sum(1 for v in values if v < target)
    equal = sum(1 for v in values if v == target)
    n = len(values)
    return round((below + 0.5 * equal) / n * 100, 1)


def zscore_of(values: list[float], target: float) -> float | None:
    """(target - mean) / std. Devuelve None si <2 valores. 0.0 si std=0."""
    if len(values) < 2:
        return None
    mean = statistics.mean(values)
    std = statistics.stdev(values)
    if std == 0:
        return 0.0
    return round((target - mean) / std, 2)


def _tendencia(values: list[float]) -> str:
    """Compara primer tercio vs último tercio de la serie.

    Espera que `values` venga ordenada cronológicamente (más vieja → más nueva).
    """
    n = len(values)
    if n < 6:
        return "lateral"
    k = max(2, n // 3)
    first = statistics.mean(values[:k])
    last = statistics.mean(values[-k:])
    if first == 0:
        return "lateral"
    cambio = (last - first) / abs(first)
    if abs(cambio) < _TENDENCIA_LATERAL:
        return "lateral"
    if cambio >= _TENDENCIA_FUERTE:
        return "tendencia_alcista"
    if cambio <= -_TENDENCIA_FUERTE:
        return "tendencia_bajista"
    # Cambio chico pero no nulo: lateral
    return "lateral"


def classify_level(values: list[float], current: float) -> str:
    """Clasifica `current` vs la distribución `values`.

    Devuelve uno de:
        "minimo"              — percentil < 10
        "bajo"                — 10-30
        "medio"               — 30-70 (puede ser "tendencia_alcista"/"tendencia_bajista" si hay mov.)
        "alto"                — 70-90
        "maximo"              — > 90
        "tendencia_alcista"   — medio + tendencia fuerte al alza
        "tendencia_bajista"   — medio + tendencia fuerte a la baja
        "lateral"             — medio sin tendencia
        "sin_datos"           — muestra insuficiente

    `values` debe venir ordenada cronológicamente (viejo → nuevo) para que
    la detección de tendencia funcione.
    """
    vals = [v for v in values if v is not None]
    if len(vals) < _MIN_PUNTOS:
        return "sin_datos"
    pct = percentile_of(vals, current)
    if pct is None:
        return "sin_datos"
    if pct < _PCT_MINIMO_MAX:
        return "minimo"
    if pct > _PCT_MAXIMO_MIN:
        return "maximo"
    if pct < _PCT_BAJO_MAX:
        return "bajo"
    if pct > _PCT_ALTO_MIN:
        return "alto"
    # Rango medio: decide por tendencia
    return _tendencia(vals)


def compute_stats(values: list[float], current: float) -> dict[str, Any]:
    """Paquete completo de stats sobre una serie. Tolerante a nulls."""
    vals = [v for v in values if v is not None]
    if not vals:
        return {
            "actual": current,
            "min": None,
            "max": None,
            "media": None,
            "desvio": None,
            "percentil_actual": None,
            "zscore_actual": None,
            "clasificacion": "sin_datos",
            "n_observaciones": 0,
        }
    return {
        "actual": current,
        "min": min(vals),
        "max": max(vals),
        "media": round(statistics.mean(vals), 6),
        "desvio": round(statistics.stdev(vals), 6) if len(vals) >= 2 else 0.0,
        "percentil_actual": percentile_of(vals, current),
        "zscore_actual": zscore_of(vals, current),
        "clasificacion": classify_level(vals, current),
        "n_observaciones": len(vals),
    }


def cambio_pct(values: list[float], current: float, lookback_idx: int) -> float | None:
    """Cambio % de `current` vs el valor de hace `lookback_idx` observaciones.

    Si la serie tiene menos observaciones que lookback_idx, devuelve None.
    Útil para calcular cambio_dia/semana/mes dado que la granularidad del
    caller es conocida (daily, intradiario, etc.).
    """
    vals = [v for v in values if v is not None]
    if len(vals) < lookback_idx + 1:
        return None
    previo = vals[-lookback_idx - 1]
    if previo == 0:
        return None
    return round((current - previo) / abs(previo) * 100, 2)
