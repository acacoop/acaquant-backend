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
    """Ventana para encontrar el día hábil previo. El pivot diario usa SOLO
    la última vela de esta ventana (flag `solo_ultima`): el H/L/C tiene que
    salir todo del MISMO día previo para proyectar los pivots de mañana. El
    rango sólo necesita ser lo bastante ancho para incluir el último día
    hábil (10 días cubre fines de semana largos y feriados)."""
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


def _ohlc_en_rango(
    ticker: str, fecha_desde: datetime, fecha_hasta: datetime,
    solo_ultima: bool = False,
) -> dict | None:
    """Lee Trading.PreciosAcciones y agrega H/L/C del rango [desde, hasta).

    H = max de todos los high del rango.
    L = min de todos los low del rango.
    C = close del último doc cronológicamente del rango.

    Si `solo_ultima` es True usa SOLO la última vela del rango (el día
    previo): el H/L/C sale todo del MISMO día. Lo usa el timeframe diario —
    sus pivots se proyectan del día anterior, NO se agregan varios días.

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
    if solo_ultima:
        docs = docs[-1:]

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


def _frame(
    label: str, ticker: str, fecha_desde: datetime, fecha_hasta: datetime,
    solo_ultima: bool = False,
) -> dict | None:
    """Construye un frame (timeframe) con OHLC + levels. None si no hay data."""
    ohlc = _ohlc_en_rango(ticker, fecha_desde, fecha_hasta, solo_ultima=solo_ultima)
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
            "diario":  _frame("Diario",  ticker, diario_desde,  diario_hasta, solo_ultima=True),
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


# ──────────────────────────────────────────────────────────────────────────
# Debug — detalle paso a paso del cálculo (panel Manager → Validaciones)
# ──────────────────────────────────────────────────────────────────────────


def _debug_frame(
    label: str, ticker: str, fecha_desde: datetime, fecha_hasta: datetime,
    solo_ultima: bool = False,
) -> dict:
    """Versión verbosa de `_frame` para el panel de debug.

    Devuelve la ventana de fechas consultada, TODAS las velas usadas, de qué
    vela sale cada H/L/C, la fórmula Floor Trader con los números reales y
    los niveles. No cambia el cálculo — solo lo expone paso a paso.

    Con `solo_ultima` (timeframe diario) deja únicamente la última vela: el
    pivot diario se proyecta del día previo, no agrega varios días.
    """
    from core.mongo import get_mongo_client

    col = get_mongo_client()["Trading"]["PreciosAcciones"]
    docs = list(col.find(
        {"ticker": ticker, "fecha": {"$gte": fecha_desde, "$lt": fecha_hasta}},
        projection={"_id": 0, "fecha": 1, "high": 1, "low": 1, "close": 1},
        sort=[("fecha", 1)],
    ))
    if solo_ultima and docs:
        docs = docs[-1:]
    base = {
        "label":       label,
        "rango_desde": fecha_desde,
        "rango_hasta": fecha_hasta,
        "n_velas":     len(docs),
        "velas":       docs,
    }
    velas_h = [d for d in docs if d.get("high") is not None]
    velas_l = [d for d in docs if d.get("low") is not None]
    velas_c = [d for d in docs if d.get("close") is not None]
    if not velas_h or not velas_l or not velas_c:
        return {**base, "ok": False, "motivo": "Sin velas con high/low/close en el rango."}

    vela_h = max(velas_h, key=lambda d: d["high"])
    vela_l = min(velas_l, key=lambda d: d["low"])
    vela_c = velas_c[-1]            # docs ya vienen ordenados por fecha asc
    h, l, c = vela_h["high"], vela_l["low"], vela_c["close"]

    levels = calcular(high=h, low=l, close=c)
    pp = levels["pp"]
    rango = h - l
    formula = [
        {"paso": "PP = (H + L + C) / 3", "valor": f"({h:.4f} + {l:.4f} + {c:.4f}) / 3 = {pp:.4f}"},
        {"paso": "R1 = 2·PP − L",        "valor": f"2·{pp:.4f} − {l:.4f} = {levels['r1']:.4f}"},
        {"paso": "S1 = 2·PP − H",        "valor": f"2·{pp:.4f} − {h:.4f} = {levels['s1']:.4f}"},
        {"paso": "R2 = PP + (H − L)",    "valor": f"{pp:.4f} + {rango:.4f} = {levels['r2']:.4f}"},
        {"paso": "S2 = PP − (H − L)",    "valor": f"{pp:.4f} − {rango:.4f} = {levels['s2']:.4f}"},
        {"paso": "R3 = H + 2·(PP − L)",  "valor": f"{h:.4f} + 2·({pp:.4f} − {l:.4f}) = {levels['r3']:.4f}"},
        {"paso": "S3 = L − 2·(H − PP)",  "valor": f"{l:.4f} − 2·({h:.4f} − {pp:.4f}) = {levels['s3']:.4f}"},
    ]
    return {
        **base,
        "ok":      True,
        "h":       {"valor": h, "fecha": vela_h["fecha"]},
        "l":       {"valor": l, "fecha": vela_l["fecha"]},
        "c":       {"valor": c, "fecha": vela_c["fecha"]},
        "formula": formula,
        "levels":  levels,
    }


def debug_4_timeframes(ticker: str) -> dict:
    """Como `obtener_4_timeframes` pero con el detalle COMPLETO del cálculo:
    ventana consultada, velas usadas, de qué vela sale cada H/L/C, fórmula
    con números y niveles. Alimenta el panel de Manager → Validaciones."""
    from core.mongo import get_mongo_client

    col = get_mongo_client()["Trading"]["PreciosAcciones"]
    last_doc = col.find_one(
        {"ticker": ticker},
        projection={"_id": 0, "fecha": 1, "close": 1},
        sort=[("fecha", -1)],
    )

    diario_desde, diario_hasta   = _rango_diario_previo()
    semanal_desde, semanal_hasta = _rango_semanal_previo()
    mensual_desde, mensual_hasta = _rango_mensual_previo()
    anual_desde, anual_hasta     = _rango_anual_previo()

    return {
        "ticker":     ticker,
        "last":       last_doc.get("close") if last_doc else None,
        "last_fecha": last_doc.get("fecha") if last_doc else None,
        "frames": {
            "diario":  _debug_frame("Diario",  ticker, diario_desde,  diario_hasta, solo_ultima=True),
            "semanal": _debug_frame("Semanal", ticker, semanal_desde, semanal_hasta),
            "mensual": _debug_frame("Mensual", ticker, mensual_desde, mensual_hasta),
            "anual":   _debug_frame("Anual",   ticker, anual_desde,   anual_hasta),
        },
    }
