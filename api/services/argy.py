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


_CAMPOS_DOLAR = ("mep", "ccl", "canje")


def _series_dolar() -> dict[str, list[tuple[date, float]]]:
    """Series históricas de mep/ccl/canje desde valuaciones.dolar (SQL-native):
    último valor por día (ART), ordenado ascendente, por campo.

    Las tres salen de UNA sola pasada de la tabla — este endpoint corre cada 5s
    (TTL del cache) y las tres series se piden siempre juntas."""
    from core import dolar_sql
    by_day: dict[str, dict[date, float]] = {c: {} for c in _CAMPOS_DOLAR}
    for fecha_s, vals in dolar_sql.por_dia_multi(_CAMPOS_DOLAR).items():
        try:
            f = datetime.strptime(fecha_s, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        for campo, v in vals.items():
            try:
                by_day[campo][f] = float(v)
            except (ValueError, TypeError):
                continue
    return {c: sorted(by_day[c].items()) for c in _CAMPOS_DOLAR}


def _serie_caucion(moneda: str) -> list[tuple[date, float]]:
    """Serie histórica de tna_cierre de caución por moneda desde SQL (mercado_hist Caucion).
    Antes leía Mongo Trading.Caucion (DROPEADA) → la watchlist mostraba —."""
    from api.services import mercado_hist_sql
    docs = mercado_hist_sql.get_historico_caucion(moneda=moneda)
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
    out.sort()  # cronológico ascendente (independiente del orden del reader)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Live values
# ─────────────────────────────────────────────────────────────────────────────


# Si DolarSnapshot._id="current" tiene timestamp más viejo que esto, lo
# consideramos stale y caemos al último doc cron-escrito de Valuaciones.Dolar.
# El motor escribe cada 5s, así que 60s es generoso pero suficiente para
# absorber jitter de WS/Mongo sin servir data podrida (incidente 2026-05-05:
# motor wedged tras Atlas pause, snapshot quedó stuck con valores de ayer).
_DOLAR_SNAPSHOT_MAX_AGE_S = 60


def _live_dolar() -> dict:
    """Snapshot live de MEP/CCL/canje desde valuaciones.dolar_snapshot,
    fallback al último valuaciones.dolar si el snapshot está ausente o stale."""
    from core import dolar_sql
    snap = dolar_sql.snapshot_live()
    if snap and snap.get("mep") is not None:
        ts = snap.get("timestamp")
        is_fresh = (
            isinstance(ts, datetime)
            and (datetime.now(ts.tzinfo) - ts).total_seconds() <= _DOLAR_SNAPSHOT_MAX_AGE_S
        )
        if is_fresh:
            return {
                "mep":   snap.get("mep"),
                "ccl":   snap.get("ccl"),
                "canje": snap.get("canje"),
                "ts":    ts,
                "src":   "live",
            }
    last = dolar_sql.ultimo("mep")
    if last:
        return {
            "mep":   last.get("mep"),
            "ccl":   last.get("ccl"),
            "canje": last.get("canje"),
            "ts":    last.get("timestamp"),
            "src":   "cron",
        }
    return {"mep": None, "ccl": None, "canje": None, "ts": None, "src": "none"}


def _live_caucion(moneda: str) -> dict:
    """Snapshot live de caución desde SQL (mercado.caucion_snapshot).
    Antes leía Mongo Trading.CaucionSnapshot (DROPEADA) → la watchlist mostraba —."""
    from api.services import mercado_hist_sql
    docs = mercado_hist_sql.get_caucion(moneda=moneda)
    snap = docs[0] if docs else None
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


def _live_oficial(casa: str) -> dict:
    """Último snapshot live del oficial — feed MAE mayorista UST$T.

    Fuente única en `core.dolar_oficial.mid_oficial_live` (script local
    en PC oficina). MAE devuelve precioUltimo + variación % del día.
    """
    from core.dolar_oficial import mid_oficial_live

    return mid_oficial_live(casa)


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
    series = _series_dolar()
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

    # ── Dólar oficial (MAE mayorista UST$T) ──
    # Spot vivo + %Día desde Valuaciones.DolarOficialLive (script
    # `mae_forex.py` corriendo en PC oficina, IP no bloqueada por MAE).
    # 7d/MTD/YTD quedan en None — la fuente histórica anterior
    # (Valuaciones.DolarOficial / dolarapi.com) se eliminó el 2026-05-04.
    # Cuando DolarOficialLive acumule history se puede agregar la serie acá.
    live_oficial = _live_oficial("oficial")
    var_mae = live_oficial.get("variacion")
    out.append({
        "label":   "DOLAR OFICIAL",
        "value":   live_oficial.get("value"),
        "unit":    "$",
        "ret_day": round(var_mae, 2) if var_mae is not None else None,
        "ret_7d":  None,
        "ret_mtd": None,
        "ret_ytd": None,
        "ts":      live_oficial.get("ts").isoformat() if isinstance(live_oficial.get("ts"), datetime) else None,
        "source":  live_oficial.get("source"),
    })

    # ── Riesgo país (bps) — SQL-only (macro.series_macro, vía jobs/argentina_datos.py) ──
    from core.series_macro import serie_dict
    rp = serie_dict("RiesgoPais")
    if rp:
        rp_serie = [(date.fromisoformat(f), v) for f, v in sorted(rp.items())]
        ult_fecha, actual = rp_serie[-1][0], (rp_serie[-1][1] or None)
        out.append({
            "label":   "RIESGO PAÍS",
            "value":   actual,
            "unit":    "bps",
            "ret_day": _ret_pct(actual, _last_le(rp_serie, anchors["day"])),
            "ret_7d":  _ret_pct(actual, _last_le(rp_serie, anchors["7d"])),
            "ret_mtd": _ret_pct(actual, _last_le(rp_serie, anchors["mtd"])),
            "ret_ytd": _ret_pct(actual, _last_le(rp_serie, anchors["ytd"])),
            "ts":      ult_fecha.isoformat(),
            "source":  "argentinadatos.com",
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
