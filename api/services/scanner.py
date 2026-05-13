"""api/services/scanner.py — vista Scanner del módulo Renta Variable.

Lee `Trading.Cedears` (master categórico: sector/industria/region/país) y
`Trading.CedearsSnapshot` (live escrito por `engines/motor_cedears.py` cada
1s) y los joinea por ticker. Devuelve un row por CEDEAR activo con sus
métricas operativas (last, intraday %, vs 1D %, retorno USD descontando
CCL).

Retorno USD (`vs_1d_usd_pct`): los CEDEARs se mueven en ARS pero el
underlying es un activo USD — parte del movimiento ARS es la devaluación
implícita del CCL. Descontamos esa componente para mostrar el retorno
"real" del subyacente:

    vs_1d_usd_pct = ((1 + cedear_1d/100) / (1 + ccl_1d/100) - 1) × 100

Donde ccl_1d = (ccl_live / ccl_cierre_ayer - 1) × 100.

Patrón: una sola lectura del CCL por request (en `_ccl_pct_vs_1d`), no
por cada ticker. El cache TTL 5s del @cached evita machacar Mongo bajo
polling del frontend.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from api.cache import cached
from api.db import get_db_trading, get_db_valuaciones


# Si Valuaciones.DolarSnapshot._id="current" tiene timestamp más viejo que
# esto, lo consideramos stale y caemos al último Valuaciones.Dolar.
# Mismo valor que usa argy._live_dolar — coherencia entre vistas que
# muestran CCL live.
_DOLAR_SNAPSHOT_MAX_AGE_S = 60


@cached(ttl=5)
def get_ccl_live() -> dict:
    """CCL live + variación 1D vs cierre día previo.

    Compartido entre el endpoint `/api/scanner/ccl` (KPI del shell) y
    `get_cedears_scanner()` (cálculo de retorno USD por ticker). Cacheado
    5s para que ambos endpoints peguen una vez por ventana sin importar
    cuántos requests entren.

    Returns:
        {value, vs_1d_pct, ts}. Cualquier campo puede ser None si no hay
        live (motor dolares caído / Atlas pause) o no hay cierre previo
        (primer día del calendario / colección vacía).

    Fuentes (mismo orden de prioridad que api/services/argy._live_dolar):
      1. Valuaciones.DolarSnapshot._id='current' si timestamp ≤ 60s.
      2. Fallback al último doc con ccl en Valuaciones.Dolar.
    """
    db = get_db_valuaciones()

    # ── Live ────────────────────────────────────────────────────────
    ccl_value: float | None = None
    ts: datetime | None = None

    snap = db["DolarSnapshot"].find_one(
        {"_id": "current"}, {"ccl": 1, "timestamp": 1}
    )
    if snap:
        snap_ts = snap.get("timestamp")
        if isinstance(snap_ts, datetime):
            age = (datetime.now(snap_ts.tzinfo) - snap_ts).total_seconds()
            if age <= _DOLAR_SNAPSHOT_MAX_AGE_S and snap.get("ccl") is not None:
                ccl_value = float(snap["ccl"])
                ts = snap_ts

    if ccl_value is None:
        latest = db["Dolar"].find_one(
            {"ccl": {"$ne": None}},
            {"_id": 0, "ccl": 1, "timestamp": 1},
            sort=[("timestamp", -1)],
        )
        if latest and latest.get("ccl"):
            ccl_value = float(latest["ccl"])
            ts = latest.get("timestamp")

    if not ccl_value or ccl_value <= 0:
        return {"value": None, "vs_1d_pct": None, "ts": None}

    # ── Cierre día previo ──────────────────────────────────────────
    # Último doc con timestamp < hoy 00:00 UTC. Si ayer no hubo doc
    # (feriado, weekend), el query devuelve el último día hábil — es
    # exactamente lo que queremos para "1D".
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    prev = db["Dolar"].find_one(
        {"ccl": {"$ne": None}, "timestamp": {"$lt": today_start}},
        {"_id": 0, "ccl": 1},
        sort=[("timestamp", -1)],
    )
    vs_1d_pct: float | None = None
    if prev and prev.get("ccl"):
        ccl_prev = float(prev["ccl"])
        if ccl_prev > 0:
            vs_1d_pct = (ccl_value / ccl_prev - 1) * 100

    return {
        "value":     ccl_value,
        "vs_1d_pct": vs_1d_pct,
        "ts":        ts.isoformat() if isinstance(ts, datetime) else None,
    }


def _adr_metrics_para_todos(tickers: list[str]) -> dict[str, dict]:
    """Calcula métricas ADR (USD del underlying) para todos los tickers
    en una sola query a Trading.PreciosAcciones.

    Para cada ticker devuelve:
      adr_last:        último close USD
      adr_fecha:       ISO date del último close
      adr_vs_1d_pct:   (last/prev_close − 1) × 100
      adr_ret_7d_pct:  (last/close_~7d_atras − 1) × 100
      adr_ret_mtd_pct: (last/close_1er_dia_mes − 1) × 100
      adr_ret_ytd_pct: (last/close_1er_dia_anio − 1) × 100

    None en cualquier campo si la serie es muy corta para ese anchor.
    Cargo TODA la serie de los 27 tickers en una pasada (≈6.8k docs,
    trivial) y opero en memoria — evita N+1 queries.
    """
    if not tickers:
        return {}

    db = get_db_trading()
    docs_by_ticker: dict[str, list[dict]] = {}
    for d in db["PreciosAcciones"].find(
        {"ticker": {"$in": tickers}},
        projection={"_id": 0, "ticker": 1, "fecha": 1, "close": 1},
    ):
        docs_by_ticker.setdefault(d["ticker"], []).append(d)

    # Anchors temporales — calculados una sola vez para todos los tickers.
    hoy = datetime.now(timezone.utc)
    anchor_7d  = hoy - timedelta(days=7)
    anchor_mtd = hoy.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    anchor_ytd = hoy.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    out: dict[str, dict] = {}
    for ticker in tickers:
        docs = docs_by_ticker.get(ticker, [])
        if not docs:
            out[ticker] = {
                "adr_last":         None,
                "adr_fecha":        None,
                "adr_vs_1d_pct":    None,
                "adr_ret_7d_pct":   None,
                "adr_ret_mtd_pct":  None,
                "adr_ret_ytd_pct":  None,
            }
            continue

        docs.sort(key=lambda x: x["fecha"])
        last_doc   = docs[-1]
        last_close = last_doc.get("close")
        last_fecha = last_doc.get("fecha")

        # 1D = vs penúltimo doc
        vs_1d = None
        if len(docs) >= 2:
            prev_close = docs[-2].get("close")
            if last_close and prev_close:
                vs_1d = ((last_close / prev_close) - 1) * 100

        # Anchor returns — close más reciente con fecha ≤ anchor_target.
        def _ret_vs(anchor_ts: datetime) -> float | None:
            if last_close is None:
                return None
            for d in reversed(docs):
                if d["fecha"] <= anchor_ts:
                    base = d.get("close")
                    if base and base > 0:
                        return ((last_close / base) - 1) * 100
                    return None
            return None  # toda la serie es posterior al anchor

        out[ticker] = {
            "adr_last":         last_close,
            "adr_fecha":        last_fecha.isoformat() if isinstance(last_fecha, datetime) else None,
            "adr_vs_1d_pct":    vs_1d,
            "adr_ret_7d_pct":   _ret_vs(anchor_7d),
            "adr_ret_mtd_pct":  _ret_vs(anchor_mtd),
            "adr_ret_ytd_pct":  _ret_vs(anchor_ytd),
        }
    return out


@cached(ttl=5)
def get_cedears_scanner() -> list[dict]:
    """Master + snapshot joined por ticker, con métricas operativas.

    Incluye dos sets de métricas:
      - CEDEAR (live ARS desde Trading.CedearsSnapshot)
      - ADR (USD del underlying desde Trading.PreciosAcciones — EOD daily)

    Tickers sin snapshot CEDEAR (motor recién arrancado) → métricas CEDEAR
    en None. Tickers sin histórico ADR → métricas ADR en None. El
    frontend muestra '--' en ambos casos.
    """
    db = get_db_trading()
    master = list(db["Cedears"].find(
        {"activo": True},
        {"_id": 0},
    ))
    snapshots = {
        s["ticker"]: s
        for s in db["CedearsSnapshot"].find({}, {"_id": 0})
    }
    # ADR metrics — una sola pasada por Trading.PreciosAcciones.
    adr_metrics = _adr_metrics_para_todos([m["ticker_corto"] for m in master])

    # CCL una sola vez por request — no por ticker. get_ccl_live está
    # cacheada también 5s, así que esta llamada es prácticamente gratis
    # cuando viene del mismo cache window. Si es None, todas las columnas
    # USD del scanner muestran '--' (no rompe nada).
    ccl_1d = get_ccl_live().get("vs_1d_pct")

    out: list[dict] = []
    for m in master:
        s = snapshots.get(m["ticker"], {})
        last  = float(s.get("last")  or 0)
        open_ = float(s.get("open")  or 0)
        close = float(s.get("close") or 0)
        high  = float(s.get("high")  or 0)
        low   = float(s.get("low")   or 0)

        intraday = ((last / open_) - 1) * 100 if last > 0 and open_ > 0 else None
        vs_1d    = ((last / close) - 1) * 100 if last > 0 and close > 0 else None

        # Retorno USD real: descuenta variación del CCL al cedear_1d.
        # Si CEDEAR sube X% y CCL sube X%, el activo en USD se mantuvo (0%).
        vs_1d_usd = None
        if vs_1d is not None and ccl_1d is not None:
            vs_1d_usd = ((1 + vs_1d / 100) / (1 + ccl_1d / 100) - 1) * 100

        adr = adr_metrics.get(m["ticker_corto"], {})
        out.append({
            "ticker_corto":     m["ticker_corto"],
            "underlying":       m.get("underlying"),
            "ratio_cedear":     m.get("ratio_cedear"),
            "sector":           m.get("sector"),
            "industria":        m.get("industria"),
            "region":           m.get("region"),
            "pais":             m.get("pais"),
            # CEDEAR (ARS live)
            "last":             last if last > 0 else None,
            "open":             open_ if open_ > 0 else None,
            "high":             high if high > 0 else None,
            "low":              low if low > 0 else None,
            "close":            close if close > 0 else None,
            "intraday_pct":     intraday,
            "vs_1d_pct":        vs_1d,
            "vs_1d_usd_pct":    vs_1d_usd,
            # ADR (USD EOD del underlying)
            "adr_last":         adr.get("adr_last"),
            "adr_fecha":        adr.get("adr_fecha"),
            "adr_vs_1d_pct":    adr.get("adr_vs_1d_pct"),
            "adr_ret_7d_pct":   adr.get("adr_ret_7d_pct"),
            "adr_ret_mtd_pct":  adr.get("adr_ret_mtd_pct"),
            "adr_ret_ytd_pct":  adr.get("adr_ret_ytd_pct"),
            "updated_at":       s.get("updated_at"),
        })
    return out
