"""Wrapper que enriquece respuestas de tools con metadata temporal.

Objetivo: que el modelo pueda razonar sobre la frescura del dato. En vez de
devolver solo `{"data": ...}`, agregamos `_meta` con timestamps y staleness.

Uso desde dispatch():

    meta = compute_meta(data, source="/api/cotizaciones/renta-fija")
    return {"ok": True, "data": data, "_meta": meta}

El modelo ve la metadata y puede generar disclaimers tipo "precio al cierre
de ayer" o "dato intradía de hace 3 min" sin inventar.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

# Campos que típicamente contienen un timestamp en las responses de la API
_TS_KEYS = ("updated_at", "timestamp", "ts", "as_of", "fecha", "snapshot_ts", "last_update")

# Umbrales (segundos) para clasificar staleness
_STALE_THRESHOLD_S = 120       # < 2min → fresh
_VERY_STALE_THRESHOLD_S = 900  # < 15min → stale, más → very_stale


def _to_epoch(v: Any) -> float | None:
    """Convierte string ISO, datetime, o número a epoch seconds. None si no parsea."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        # Si es epoch (>1e9 = después de 2001) lo aceptamos; si no, puede ser año o algo raro
        return float(v) if v > 1_000_000_000 else None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=UTC)
        return v.timestamp()
    if isinstance(v, str):
        try:
            # ISO 8601 con o sin Z
            s = v.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.timestamp()
        except (ValueError, TypeError):
            return None
    return None


def _extract_timestamps(obj: Any, depth: int = 0, max_depth: int = 4) -> list[float]:
    """Busca recursivamente timestamps en una estructura anidada. Devuelve lista de epochs."""
    if depth > max_depth:
        return []
    out: list[float] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _TS_KEYS:
                t = _to_epoch(v)
                if t is not None:
                    out.append(t)
            elif isinstance(v, (dict, list)):
                out.extend(_extract_timestamps(v, depth + 1, max_depth))
    elif isinstance(obj, list):
        # Limitamos a los primeros 50 items para no hacer recursión cara en listas grandes
        for item in obj[:50]:
            out.extend(_extract_timestamps(item, depth + 1, max_depth))
    return out


def _classify_staleness(age_s: float | None) -> str:
    if age_s is None:
        return "unknown"
    if age_s < _STALE_THRESHOLD_S:
        return "fresh"
    if age_s < _VERY_STALE_THRESHOLD_S:
        return "stale"
    return "very_stale"


def compute_meta(data: Any, source: str = "") -> dict[str, Any]:
    """Calcula metadata temporal para una respuesta de tool.

    Devuelve:
        {
          "as_of_ts": int,       # epoch en que se hizo la call (now)
          "data_ts": int | None, # epoch del dato más reciente encontrado
          "age_s":   int | None, # segundos entre data_ts y now
          "staleness": "fresh" | "stale" | "very_stale" | "unknown",
          "source":  str,        # endpoint llamado
        }
    """
    now = time.time()
    timestamps = _extract_timestamps(data)
    data_ts = max(timestamps) if timestamps else None
    age_s = (now - data_ts) if data_ts is not None else None

    return {
        "as_of_ts": int(now),
        "data_ts": int(data_ts) if data_ts is not None else None,
        "age_s": int(age_s) if age_s is not None else None,
        "staleness": _classify_staleness(age_s),
        "source": source,
    }
