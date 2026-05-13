"""pivot_points.py — Floor Trader Pivot Points sobre Trading.PreciosAcciones.

Filosofía: el cálculo NUNCA hardcodea fechas. Para "hoy" siempre se usa la
ÚLTIMA RUEDA disponible en la colección como referencia (= la rueda más
reciente con fecha < hoy en UTC). Mañana, esa última rueda automáticamente
es la de hoy. Se rueda solo, no se mantiene.

Fórmulas Floor Trader (las clásicas, las que usa Bloomberg / Reuters):
    PP = (H + L + C) / 3
    R1 = 2 × PP − L
    S1 = 2 × PP − H
    R2 = PP + (H − L)
    S2 = PP − (H − L)
    R3 = H + 2 × (PP − L)
    S3 = L − 2 × (H − PP)

Donde H, L, C son los del DÍA HÁBIL PREVIO.

Uso (puro Python):
    from quant.pivot_points import calcular, obtener_para_ticker
    levels = calcular(high=223.75, low=214.92, close=220.78)
    # → {pp, r1, r2, r3, s1, s2, s3}

    levels = obtener_para_ticker("NVDA")  # lee de Trading.PreciosAcciones
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TypedDict


class PivotLevels(TypedDict):
    pp: float
    r1: float
    r2: float
    r3: float
    s1: float
    s2: float
    s3: float


def calcular(high: float, low: float, close: float) -> PivotLevels:
    """Pivot Floor Trader sobre OHLC del día previo.

    Args:
        high: máximo del día previo
        low:  mínimo del día previo
        close: cierre del día previo

    Returns:
        Dict con 7 niveles {pp, r1, r2, r3, s1, s2, s3}.
    """
    pp = (high + low + close) / 3.0
    rango = high - low
    return PivotLevels(
        pp=pp,
        r1=2 * pp - low,
        s1=2 * pp - high,
        r2=pp + rango,
        s2=pp - rango,
        r3=high + 2 * (pp - low),
        s3=low - 2 * (high - pp),
    )


def obtener_para_ticker(ticker: str) -> dict | None:
    """Lee de Trading.PreciosAcciones la última rueda con fecha < hoy UTC
    y calcula los pivots sobre ese OHLC.

    Returns:
        {
          fecha_ref:  ISODate("2026-05-12T00:00:00Z"),   // de qué día se sacó OHLC
          high, low, close:                              // OHLC base
          levels: {pp, r1, ..., s3},
        }
        O None si no hay data previa para ese ticker.

    Importa get_mongo_client dentro del cuerpo para que `quant/` siga
    siendo importable sin depender de la infra (regla del CLAUDE.md).
    """
    from core.mongo import get_mongo_client

    col = get_mongo_client()["Trading"]["PreciosAcciones"]
    hoy = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    # Última rueda hábil ANTES de hoy. Time series collections aceptan find
    # con sort sobre el timeField sin sufrir performance issues (índice
    # automático).
    doc = col.find_one(
        {"ticker": ticker, "fecha": {"$lt": hoy}},
        sort=[("fecha", -1)],
        projection={"_id": 0, "fecha": 1, "high": 1, "low": 1, "close": 1},
    )
    if not doc:
        return None

    high  = doc.get("high")
    low   = doc.get("low")
    close = doc.get("close")
    if high is None or low is None or close is None:
        return None

    return {
        "fecha_ref": doc["fecha"],
        "high":      high,
        "low":       low,
        "close":     close,
        "levels":    calcular(high=high, low=low, close=close),
    }
