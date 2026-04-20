"""Invariantes del dominio (renta fija argentina) validados sobre tool outputs.

Los checks acá son **conservadores**: solo flaggean violaciones que son
matemáticamente imposibles o fuera de toda lógica financiera. Falsos positivos
son más costosos que falsos negativos (rompen la UX del asistente).

Se corren en `dispatch()` después del fetch exitoso. Si hay violaciones, se
anexan a `_meta.warnings` y el modelo las incorpora al disclaimers (via
regla #8 del system prompt).

Agregar un check nuevo:
    1. Definir función `_check_X(data) -> list[str]` que devuelva warnings.
    2. Agregarla a `CHECKS` abajo.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _iter_items(data: Any) -> Iterator[dict]:
    """Yields dict items, ya sea data es lista, dict top-level, o dict con listas adentro."""
    if isinstance(data, list):
        for x in data:
            if isinstance(x, dict):
                yield x
    elif isinstance(data, dict):
        yield data
        for v in data.values():
            if isinstance(v, list):
                for x in v:
                    if isinstance(x, dict):
                        yield x


def _label(item: dict) -> str:
    """Identificador humano-legible del item para los warnings."""
    for k in ("ticker", "ticker_corto", "instrumento", "symbol"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return "?"


def _as_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Checks individuales
# ─────────────────────────────────────────────────────────────────────────────


def _check_paridad(data: Any) -> list[str]:
    """Paridad (%) debería estar entre 0 y 150 para bonos argentinos. Fuera de
    ese rango huele a bug de cálculo (ej. CER no normalizado, división por cero)."""
    out: list[str] = []
    for item in _iter_items(data):
        p = _as_float(item.get("paridad"))
        if p is None:
            continue
        if not (0 <= p <= 150):
            out.append(f"paridad={p:.2f}% fuera de [0,150] en {_label(item)}")
    return out


def _check_amortizaciones_suman_100(data: Any) -> list[str]:
    """La suma de amortizaciones_pct de un bono con flujos debe dar ~100 (tolerancia 1)."""
    out: list[str] = []
    for item in _iter_items(data):
        flujos = item.get("flujos")
        if not isinstance(flujos, list) or not flujos:
            continue
        suma = 0.0
        saw_amort = False
        for f in flujos:
            if not isinstance(f, dict):
                continue
            a = _as_float(f.get("amortizacion_pct"))
            if a is None:
                a = _as_float(f.get("amortizacion"))
            if a is not None:
                suma += a
                saw_amort = True
        if saw_amort and abs(suma - 100.0) > 1.0:
            out.append(f"amortizaciones suman {suma:.1f} (esperado 100) en {_label(item)}")
    return out


def _check_duration_positiva(data: Any) -> list[str]:
    """Duration negativa es imposible para bonos con cupones positivos."""
    out: list[str] = []
    for item in _iter_items(data):
        d = _as_float(item.get("duration"))
        if d is None:
            metrics = item.get("metrics")
            if isinstance(metrics, dict):
                d = _as_float(metrics.get("duration"))
        if d is None:
            continue
        if d < 0:
            out.append(f"duration={d:.3f} negativa en {_label(item)}")
    return out


def _check_residual_monotonico(data: Any) -> list[str]:
    """El residual_previo_pct de los flujos debe ser monótonamente no-creciente
    (una vez que empieza a amortizar, no puede volver a subir)."""
    out: list[str] = []
    for item in _iter_items(data):
        flujos = item.get("flujos")
        if not isinstance(flujos, list) or len(flujos) < 2:
            continue
        residuales: list[float] = []
        for f in flujos:
            if not isinstance(f, dict):
                continue
            r = _as_float(f.get("residual_previo_pct"))
            if r is None:
                r = _as_float(f.get("valor_residual"))
            if r is not None:
                residuales.append(r)
        for i in range(1, len(residuales)):
            if residuales[i] > residuales[i - 1] + 0.01:
                out.append(
                    f"residual no-monotónico en {_label(item)}: "
                    f"flujo {i} tiene {residuales[i]:.1f}, anterior {residuales[i - 1]:.1f}"
                )
                break
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Registry + entry point
# ─────────────────────────────────────────────────────────────────────────────

CHECKS = [
    _check_paridad,
    _check_amortizaciones_suman_100,
    _check_duration_positiva,
    _check_residual_monotonico,
]


def run_invariants(data: Any) -> list[str]:
    """Corre todos los checks sobre data. Devuelve warnings (vacía si todo OK).

    NO falla nunca: si un check explota internamente, lo ignora. Los invariantes
    son 'best effort' para loggear anomalías, no bloquear responses.
    """
    warnings: list[str] = []
    for check in CHECKS:
        try:
            warnings.extend(check(data))
        except Exception:
            pass
    return warnings
