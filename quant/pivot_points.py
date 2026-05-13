"""pivot_points.py — Floor Trader Pivot Points sobre Trading.PreciosAcciones.

Filosofía: el cálculo NUNCA hardcodea fechas. Cada timeframe pide
dinámicamente "el período anterior cerrado" (último día hábil, última
semana ISO, mes calendario previo, año calendario previo). Se rueda
solo, no se mantiene.

Fórmulas Floor Trader (las clásicas, las que usa Bloomberg / Reuters):
    PP = (H + L + C) / 3
    R1 = 2 × PP − L
    S1 = 2 × PP − H
    R2 = PP + (H − L)
    S2 = PP − (H − L)
    R3 = H + 2 × (PP − L)
    S3 = L − 2 × (H − PP)

Donde H/L/C son del período previo (día, semana, mes, año).

Uso (puro Python):
    from quant.pivot_points import obtener_4_timeframes
    levels = obtener_4_timeframes("NVDA")
    # → {
    #     ticker: "NVDA",
    #     last: 220.78,
    #     frames: {
    #         diario:  {label, fecha_desde, fecha_hasta, h, l, c, levels},
    #         semanal: {...},
    #         mensual: {...},
    #         anual:   {...}
    #     }
    #   }
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    """Pivot Floor Trader sobre OHLC del período previo."""
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


# ──────────────────────────────────────────────────────────────────────────
# Rangos temporales — dinámicos, todos calculados a partir de "hoy"
# ──────────────────────────────────────────────────────────────────────────


def _hoy_utc_00() -> datetime:
    """Hoy a las 00:00 UTC."""
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _rango_diario_previo() -> tuple[datetime, datetime]:
    """Día hábil previo: cualquier fecha < hoy. La query toma sort desc
    limit 1, así que el rango exacto no importa mientras incluya el último
    día hábil disponible."""
    hoy = _hoy_utc_00()
    return (hoy - timedelta(days=10), hoy)


def _rango_semanal_previo() -> tuple[datetime, datetime]:
    """Semana ISO previa cerrada (lunes a viernes pasados).

    Si hoy es martes 2026-05-13, la semana previa es 2026-05-04 a 2026-05-10.
    Tomamos lunes 00:00 UTC de esta semana como límite superior exclusivo.
    """
    hoy = _hoy_utc_00()
    # weekday(): lunes=0, martes=1, ..., domingo=6
    lunes_esta_semana = hoy - timedelta(days=hoy.weekday())
    lunes_pasado      = lunes_esta_semana - timedelta(days=7)
    return (lunes_pasado, lunes_esta_semana)


def _rango_mensual_previo() -> tuple[datetime, datetime]:
    """Mes calendario previo. Si hoy es 2026-05-13, devuelve [2026-04-01, 2026-05-01)."""
    hoy = _hoy_utc_00()
    primer_dia_este_mes = hoy.replace(day=1)
    # Resto 1 día para caer en el último día del mes previo, luego replace(day=1).
    ultimo_dia_mes_previo = primer_dia_este_mes - timedelta(days=1)
    primer_dia_mes_previo = ultimo_dia_mes_previo.replace(day=1)
    return (primer_dia_mes_previo, primer_dia_este_mes)


def _rango_anual_previo() -> tuple[datetime, datetime]:
    """Año calendario previo. Si hoy es 2026-05-13, devuelve [2025-01-01, 2026-01-01)."""
    hoy = _hoy_utc_00()
    primer_dia_este_anio = hoy.replace(month=1, day=1)
    primer_dia_anio_previo = primer_dia_este_anio.replace(year=primer_dia_este_anio.year - 1)
    return (primer_dia_anio_previo, primer_dia_este_anio)


# ──────────────────────────────────────────────────────────────────────────
# Lectura de Mongo + cálculo
# ──────────────────────────────────────────────────────────────────────────


def _ohlc_en_rango(ticker: str, fecha_desde: datetime, fecha_hasta: datetime) -> dict | None:
    """Lee Trading.PreciosAcciones y agrega H/L/C del rango [desde, hasta).

    H = max de todos los high del rango.
    L = min de todos los low del rango.
    C = close del último doc cronológicamente del rango.

    Returns None si no hay docs en el rango.

    `get_mongo_client` se importa dentro del cuerpo para que quant/ no
    dependa de la infra al import-time (regla de capas del CLAUDE.md).
    """
    from core.mongo import get_mongo_client

    col = get_mongo_client()["Trading"]["PreciosAcciones"]
    docs = list(col.find(
        {"ticker": ticker, "fecha": {"$gte": fecha_desde, "$lt": fecha_hasta}},
        projection={"_id": 0, "fecha": 1, "high": 1, "low": 1, "close": 1},
        sort=[("fecha", 1)],
    ))
    if not docs:
        return None

    highs  = [d["high"]  for d in docs if d.get("high")  is not None]
    lows   = [d["low"]   for d in docs if d.get("low")   is not None]
    closes = [d["close"] for d in docs if d.get("close") is not None]
    if not highs or not lows or not closes:
        return None

    return {
        "h":           max(highs),
        "l":           min(lows),
        "c":           closes[-1],
        "fecha_desde": docs[0]["fecha"],
        "fecha_hasta": docs[-1]["fecha"],
        "n_velas":     len(docs),
    }


def _frame(label: str, ticker: str, fecha_desde: datetime, fecha_hasta: datetime) -> dict | None:
    """Construye un frame (timeframe) con OHLC + levels. None si no hay data."""
    ohlc = _ohlc_en_rango(ticker, fecha_desde, fecha_hasta)
    if not ohlc:
        return None
    return {
        "label":       label,
        "fecha_desde": ohlc["fecha_desde"],
        "fecha_hasta": ohlc["fecha_hasta"],
        "n_velas":     ohlc["n_velas"],
        "h":           ohlc["h"],
        "l":           ohlc["l"],
        "c":           ohlc["c"],
        "levels":      calcular(high=ohlc["h"], low=ohlc["l"], close=ohlc["c"]),
    }


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────


def obtener_4_timeframes(ticker: str) -> dict:
    """Devuelve los 4 timeframes de pivots para un ticker + el último close
    disponible (para calcular distancias en el frontend).

    Returns:
        {
          ticker: "NVDA",
          last: 220.78,        # último close de la serie (cierre día previo)
          last_fecha: ISODate,
          frames: {
            diario:  {label, fecha_desde, fecha_hasta, n_velas, h, l, c, levels} | None,
            semanal: ...,
            mensual: ...,
            anual:   ...
          }
        }
    """
    from core.mongo import get_mongo_client

    # Último close = última vela disponible en la serie (cierre del día
    # previo hasta que el cron diario meta el de hoy).
    col = get_mongo_client()["Trading"]["PreciosAcciones"]
    last_doc = col.find_one(
        {"ticker": ticker},
        projection={"_id": 0, "fecha": 1, "close": 1},
        sort=[("fecha", -1)],
    )
    last       = last_doc.get("close") if last_doc else None
    last_fecha = last_doc.get("fecha") if last_doc else None

    diario_desde, diario_hasta   = _rango_diario_previo()
    semanal_desde, semanal_hasta = _rango_semanal_previo()
    mensual_desde, mensual_hasta = _rango_mensual_previo()
    anual_desde, anual_hasta     = _rango_anual_previo()

    return {
        "ticker":     ticker,
        "last":       last,
        "last_fecha": last_fecha,
        "frames": {
            "diario":  _frame("Diario",  ticker, diario_desde,  diario_hasta),
            "semanal": _frame("Semanal", ticker, semanal_desde, semanal_hasta),
            "mensual": _frame("Mensual", ticker, mensual_desde, mensual_hasta),
            "anual":   _frame("Anual",   ticker, anual_desde,   anual_hasta),
        },
    }


# Mantengo la API anterior por compat. Equivale a obtener_4_timeframes()["frames"]["diario"].
def obtener_para_ticker(ticker: str) -> dict | None:
    """Legacy: solo el frame diario.

    Mantenido por si algo importa esta función. Para uso nuevo, ir directo
    a obtener_4_timeframes().
    """
    res = obtener_4_timeframes(ticker)
    diario = res["frames"].get("diario")
    if not diario:
        return None
    return {
        "fecha_ref": diario["fecha_hasta"],
        "high":      diario["h"],
        "low":       diario["l"],
        "close":     diario["c"],
        "levels":    diario["levels"],
    }
