"""Capa de servicio — métricas argentinas con returns calculados.

Devuelve un agregado de las 5 métricas que el panel ARGY del frontend
muestra (MEP, CCL, canje, caución ARS, caución USD), cada una con su
valor actual y las variaciones %Día / %7d / %MTD / %YTD calculadas
contra el cierre histórico correspondiente.

Sources:
- MEP / CCL / canje:
  - Live: Valuaciones.DolarSnapshot (escrito por engines/dolares.py).
  - Histórico: Valuaciones.Dolar (escrito por engines/dolar_mep cron + cron del cierre).
- Caución ARS / USD:
  - Live: Trading.CaucionSnapshot (escrito por engines/caucion.py).
  - Histórico: Trading.Caucion (escrito por engines/caucion al apagado).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from api.cache import cached
from api.db import get_db_trading, get_db_valuaciones

# ─────────────────────────────────────────────────────────────────────────────
# Helpers de anchors históricos
# ─────────────────────────────────────────────────────────────────────────────


def _ret_pct(actual: float | None, anchor: float | None) -> float | None:
    if actual is None or anchor is None or anchor == 0:
        return None
    return round((actual - anchor) / abs(anchor) * 100, 2)


def _anchor_dates(today: date) -> dict[str, date]:
    """Fechas-target para cada anchor. Cada uno será matcheado al doc
    histórico cuyo timestamp/fecha sea <= esa fecha."""
    return {
        "day":  today - timedelta(days=1),
        "7d":   today - timedelta(days=7),
        "mtd":  today.replace(day=1),
        "ytd":  today.replace(month=1, day=1),
    }


def _last_le(series: list[tuple[date, float]], target: date) -> float | None:
    """De una serie [(fecha, valor)] ordenada asc, devuelve el último valor
    cuya fecha sea <= target. None si la serie está vacía o todo es posterior."""
    out = None
    for f, v in series:
        if f <= target:
            out = v
        else:
            break
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Series fetchers
# ─────────────────────────────────────────────────────────────────────────────


def _serie_dolar(field: str) -> list[tuple[date, float]]:
    """Serie histórica del campo `field` (mep|ccl|canje) desde Valuaciones.Dolar.
    Devuelve último valor por día, ordenado ascendente."""
    db = get_db_valuaciones()
    docs = list(
        db["Dolar"]
        .find({field: {"$ne": None}}, {"_id": 0, "timestamp": 1, field: 1})
        .sort("timestamp", 1)
    )
    # Quedarse con el último por día (cron escribe varias por día ahora).
    by_day: dict[date, float] = {}
    for d in docs:
        ts = d.get("timestamp")
        if not isinstance(ts, datetime):
            continue
        v = d.get(field)
        if v is None:
            continue
        by_day[ts.date()] = float(v)
    return sorted(by_day.items())


def _serie_caucion(moneda: str) -> list[tuple[date, float]]:
    """Serie histórica de tna_cierre de caución por moneda desde Trading.Caucion."""
    db = get_db_trading()
    docs = list(
        db["Caucion"]
        .find({"moneda": moneda.upper(), "tna_cierre": {"$ne": None}},
              {"_id": 0, "fecha": 1, "tna_cierre": 1})
        .sort("fecha", 1)
    )
    out: list[tuple[date, float]] = []
    for d in docs:
        f = d.get("fecha")
        v = d.get("tna_cierre")
        if not f or v is None:
            continue
        try:
            out.append((date.fromisoformat(str(f)[:10]), float(v)))
        except (ValueError, TypeError):
            continue
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Live values
# ─────────────────────────────────────────────────────────────────────────────


def _live_dolar() -> dict:
    """Snapshot live de MEP/CCL/canje desde Valuaciones.DolarSnapshot,
    fallback al último Valuaciones.Dolar si no hay snapshot."""
    db = get_db_valuaciones()
    snap = db["DolarSnapshot"].find_one({"_id": "current"})
    if snap and snap.get("mep") is not None:
        return {
            "mep":   snap.get("mep"),
            "ccl":   snap.get("ccl"),
            "canje": snap.get("canje"),
            "ts":    snap.get("timestamp"),
            "src":   "live",
        }
    last = db["Dolar"].find_one(
        {"mep": {"$ne": None}},
        {"_id": 0, "mep": 1, "ccl": 1, "canje": 1, "timestamp": 1},
        sort=[("timestamp", -1)],
    )
    if last:
        last["ts"] = last.pop("timestamp", None)
        last["src"] = "cron"
        return last
    return {"mep": None, "ccl": None, "canje": None, "ts": None, "src": "none"}


def _live_caucion(moneda: str) -> dict:
    """Snapshot live de caución desde Trading.CaucionSnapshot."""
    db = get_db_trading()
    snap = db["CaucionSnapshot"].find_one(
        {"moneda": moneda.upper()},
        {"_id": 0, "tna_last": 1, "tna_closing": 1, "plazo_dias": 1, "updated_at": 1},
    )
    if not snap:
        return {"value": None, "plazo_dias": None, "ts": None}
    val = snap.get("tna_last")
    if val is None:
        val = snap.get("tna_closing")
    return {
        "value":      val,
        "plazo_dias": snap.get("plazo_dias"),
        "ts":         snap.get("updated_at"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=5)
def get_argy_with_returns() -> list[dict[str, Any]]:
    """Devuelve las 5 métricas ARGY con valor actual + returns vs anchors.

    Output:
    [
      {label, value, unit, plazo_dias?, ret_day, ret_7d, ret_mtd, ret_ytd, ts, source},
      ...
    ]

    `ret_*` es porcentaje. None si no hay anchor (data muy nueva).
    Para canje y cauciones (que YA son porcentajes), `ret_*` es el delta
    en bps relativo (variación porcentual del valor).
    """
    today = date.today()
    anchors = _anchor_dates(today)

    out: list[dict[str, Any]] = []

    # ── MEP, CCL, canje ──
    dolar = _live_dolar()
    series = {
        "mep":   _serie_dolar("mep"),
        "ccl":   _serie_dolar("ccl"),
        "canje": _serie_dolar("canje"),
    }
    metas = [
        ("DOLAR MEP", "mep",   "$"),
        ("DOLAR CCL", "ccl",   "$"),
        ("CANJE",     "canje", "%"),
    ]
    for label, key, unit in metas:
        actual = dolar.get(key)
        s = series[key]
        out.append({
            "label":   label,
            "value":   actual,
            "unit":    unit,
            "ret_day": _ret_pct(actual, _last_le(s, anchors["day"])),
            "ret_7d":  _ret_pct(actual, _last_le(s, anchors["7d"])),
            "ret_mtd": _ret_pct(actual, _last_le(s, anchors["mtd"])),
            "ret_ytd": _ret_pct(actual, _last_le(s, anchors["ytd"])),
            "ts":      dolar.get("ts").isoformat() if isinstance(dolar.get("ts"), datetime) else None,
            "source":  dolar.get("src"),
        })

    # ── Caución ARS y USD ──
    for moneda, label in (("ARS", "CAUCION ARS"), ("USD", "CAUCION USD")):
        live = _live_caucion(moneda)
        actual = live.get("value")
        s = _serie_caucion(moneda)
        out.append({
            "label":      label,
            "value":      actual,
            "unit":       "%",
            "plazo_dias": live.get("plazo_dias"),
            "ret_day":    _ret_pct(actual, _last_le(s, anchors["day"])),
            "ret_7d":     _ret_pct(actual, _last_le(s, anchors["7d"])),
            "ret_mtd":    _ret_pct(actual, _last_le(s, anchors["mtd"])),
            "ret_ytd":    _ret_pct(actual, _last_le(s, anchors["ytd"])),
            "ts":         live.get("ts").isoformat() if isinstance(live.get("ts"), datetime) else None,
            "source":     "live" if live.get("ts") else "none",
        })

    return out
