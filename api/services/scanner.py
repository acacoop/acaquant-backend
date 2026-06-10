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

from datetime import UTC, datetime, timedelta

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
    today_start = datetime.now(UTC).replace(
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


def _resolve_underlying(ticker_corto: str) -> str:
    """Mapea ticker_corto (BYMA) → underlying (US ticker para
    Trading.PreciosAcciones). Para la mayoría son iguales, pero algunos
    Argentinos tienen CEDEAR con sufijo distinto (ej. YPFD CEDEAR → YPF
    ADR). Si no encuentra el doc o el campo, fallback al ticker_corto.
    """
    db = get_db_trading()
    doc = db["Cedears"].find_one(
        {"ticker_corto": ticker_corto.upper()},
        {"_id": 0, "underlying": 1},
    )
    return (doc.get("underlying") if doc else None) or ticker_corto.upper()


def _adr_metrics_para_todos(master: list[dict]) -> dict[str, dict]:
    """Calcula métricas ADR (USD del underlying) para todos los tickers
    en una sola query a Trading.PreciosAcciones.

    Recibe la lista de docs master (con ticker_corto + underlying). El
    dict de salida está keyed por ticker_corto (lo que ve el frontend),
    pero los datos vienen de PreciosAcciones que indexa por underlying.

    None en cualquier campo si la serie es muy corta para ese anchor.
    Cargo TODA la serie en una pasada y opero en memoria — N+1 queries
    evitadas.
    """
    if not master:
        return {}

    # Map ticker_corto → underlying. Default underlying = ticker_corto si
    # el master no lo tiene (compat con docs viejos).
    corto_to_underlying: dict[str, str] = {
        m["ticker_corto"]: (m.get("underlying") or m["ticker_corto"])
        for m in master
    }
    underlyings = sorted(set(corto_to_underlying.values()))

    db = get_db_trading()
    docs_by_underlying: dict[str, list[dict]] = {}
    # NOTA: Mongo Time Series guarda timeField como datetime NAIVE.
    for d in db["PreciosAcciones"].find(
        {"ticker": {"$in": underlyings}},
        projection={"_id": 0, "ticker": 1, "fecha": 1, "close": 1},
    ):
        fecha = d.get("fecha")
        if isinstance(fecha, datetime) and fecha.tzinfo is not None:
            d["fecha"] = fecha.replace(tzinfo=None)
        docs_by_underlying.setdefault(d["ticker"], []).append(d)

    # Live USD por underlying (Trading.AdrSnapshot, populado por
    # jobs/adr_live.py cada 15 min en hs US). Si no hay snapshot todavía
    # (motor recién empezando) → fallback al cierre EOD.
    live_by_underlying: dict[str, dict] = {}
    for d in db["AdrSnapshot"].find(
        {"ticker": {"$in": underlyings}},
        projection={"_id": 0, "ticker": 1, "c": 1, "pc": 1, "t": 1, "updated_at": 1},
    ):
        live_by_underlying[d["ticker"]] = d

    # Anchors temporales — naive UTC para matchear la TS.
    hoy = datetime.now(UTC).replace(tzinfo=None)
    anchor_7d  = hoy - timedelta(days=7)
    anchor_mtd = hoy.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    anchor_ytd = hoy.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    out: dict[str, dict] = {}
    for ticker_corto, underlying in corto_to_underlying.items():
        docs = docs_by_underlying.get(underlying, [])
        live = live_by_underlying.get(underlying)

        # Si no hay ni serie histórica ni live → todo None.
        if not docs and not live:
            out[ticker_corto] = {
                "adr_last":         None,
                "adr_fecha":        None,
                "adr_intraday":     None,
                "adr_vs_1d_pct":    None,
                "adr_ret_7d_pct":   None,
                "adr_ret_mtd_pct":  None,
                "adr_ret_ytd_pct":  None,
            }
            continue

        # Precio "actual": preferimos live de Finnhub si está; sino el
        # último close EOD.
        last_close: float | None = None
        last_fecha: datetime | None = None
        # ¿El precio es de HOY (intradía) o es un cierre previo? Pre-market el
        # quote de Finnhub devuelve el cierre de ayer (t = ayer); al abrir y
        # operar, t pasa a hoy. Esto es lo que el frontend usa para marcar las
        # filas que NO son live (badge "CIERRE") y que no parezca un desalce.
        adr_intraday: bool | None = None
        if live and live.get("c"):
            last_close = float(live["c"])
            last_fecha = live.get("updated_at")
            t = live.get("t")
            if isinstance(t, (int, float)) and t > 0:
                adr_intraday = (
                    datetime.fromtimestamp(t, tz=UTC).date() >= hoy.date()
                )
        elif docs:
            docs.sort(key=lambda x: x["fecha"])
            last_doc = docs[-1]
            last_close = last_doc.get("close")
            last_fecha = last_doc.get("fecha")
            adr_intraday = False  # cierre EOD — no es precio intradía
        else:
            docs.sort(key=lambda x: x["fecha"])

        # 1D: si tenemos live, usamos su `pc` (previous close de Finnhub
        # — el cierre EOD más reciente). Sino, comparamos último vs
        # penúltimo doc EOD.
        vs_1d = None
        if live and last_close and live.get("pc"):
            prev_close = float(live["pc"])
            if prev_close > 0:
                vs_1d = ((last_close / prev_close) - 1) * 100
        elif len(docs) >= 2 and last_close:
            prev_close = docs[-2].get("close")
            if prev_close:
                vs_1d = ((last_close / prev_close) - 1) * 100

        # Anchor returns 7D/MTD/YTD: numerador = last_close (live o EOD),
        # denominador = doc EOD más reciente con fecha ≤ anchor.
        docs.sort(key=lambda x: x["fecha"])  # idempotente

        def _ret_vs(anchor_ts: datetime, _docs=docs, _last_close=last_close) -> float | None:
            if _last_close is None:
                return None
            for d in reversed(_docs):
                if d["fecha"] <= anchor_ts:
                    base = d.get("close")
                    if base and base > 0:
                        return ((_last_close / base) - 1) * 100
                    return None
            return None

        out[ticker_corto] = {
            "adr_last":         last_close,
            "adr_fecha":        last_fecha.isoformat() if isinstance(last_fecha, datetime) else None,
            "adr_intraday":     adr_intraday,
            "adr_vs_1d_pct":    vs_1d,
            "adr_ret_7d_pct":   _ret_vs(anchor_7d),
            "adr_ret_mtd_pct":  _ret_vs(anchor_mtd),
            "adr_ret_ytd_pct":  _ret_vs(anchor_ytd),
        }
    return out


@cached(ttl=60)
def get_ticker_returns(ticker: str) -> dict:
    """Retornos diarios aritméticos del ticker (~252 últimos puntos)
    desde Trading.PreciosAcciones.

    El `ticker` que llega del frontend es ticker_corto (BYMA). Resuelve
    el underlying primero (caso YPFD → YPF) antes de queryar la serie.

    Usado para histograma del panel del Scanner. Cached 60s.
    """
    from quant.rolling_stats import returns_from_prices

    underlying = _resolve_underlying(ticker)
    db = get_db_trading()
    docs = list(db["PreciosAcciones"].find(
        {"ticker": underlying},
        projection={"_id": 0, "fecha": 1, "close": 1},
        sort=[("fecha", 1)],
    ))
    if not docs:
        return {
            "ticker":      ticker.upper(),
            "returns":     [],
            "last_return": None,
            "last_fecha":  None,
        }

    closes = [d["close"] for d in docs if d.get("close") is not None]
    rets = returns_from_prices(closes)
    last_fecha = docs[-1].get("fecha")
    return {
        "ticker":      ticker.upper(),
        "returns":     rets,
        "last_return": rets[-1] if rets else None,
        "last_fecha":  last_fecha.isoformat() if hasattr(last_fecha, "isoformat") else None,
    }


@cached(ttl=60)
def get_pivot_points(ticker: str) -> dict:
    """Pivot points Floor Trader en 4 timeframes (diario/semanal/mensual/
    anual) del subyacente USD del CEDEAR.

    El `ticker` que llega es ticker_corto (BYMA). Resuelve el underlying
    primero (caso YPFD → YPF) antes de calcular sobre Trading.PreciosAcciones.

    Los NIVELES de pivot salen del período previo cerrado (estáticos por
    diseño). El `last` que devuelve `obtener_4_timeframes` es el cierre EOD
    (cambia 1×/día) — acá lo pisamos con el precio live del ADR
    (`Trading.AdrSnapshot.c`, refrescado cada 15 min por `jobs/adr_live.py`)
    para que el "vs LAST" del panel se mueva durante la rueda. Si no hay
    snapshot, cae al cierre EOD. Cache 60s: el frontend pollea y el feed ADR
    solo cambia cada 15 min, así que no hace falta recalcular más seguido.
    """
    from quant.pivot_points import obtener_4_timeframes

    underlying = _resolve_underlying(ticker)
    res = obtener_4_timeframes(underlying)

    db = get_db_trading()
    snap = db["AdrSnapshot"].find_one(
        {"ticker": underlying}, {"_id": 0, "c": 1, "updated_at": 1}
    )
    if snap and snap.get("c"):
        res["last"] = float(snap["c"])
        res["last_source"] = "live"
        ua = snap.get("updated_at")
        if isinstance(ua, datetime):
            res["last_fecha"] = ua.isoformat()
    else:
        res["last_source"] = "eod"
    return res


@cached(ttl=60)
def get_quant_stats(ticker: str, window: int = 60) -> dict:
    """Stats rolling sobre Trading.PreciosAcciones: beta/alpha/correlación
    vs SPY y vs QQQ + volatilidad realizada anualizada (30d, 60d).

    Args:
        ticker: ticker_corto del activo (NVDA, AMD, etc.)
        window: días para beta/alpha/corr (default 60d hábiles).

    Returns:
        {
          ticker, last, last_fecha, n_observations,
          beta:  {spy, qqq},
          alpha: {spy, qqq},   # anualizada
          corr:  {spy, qqq},
          vol:   {d30, d60},
        }
        Cualquier campo puede ser None si la serie no alcanza al window.

    Cache TTL 60s — los cierres EOD cambian 1×/día, no hay urgencia.
    """
    from quant.rolling_stats import (
        beta_alpha,
        correlation,
        realized_vol,
        returns_from_prices,
        zscore_last,
    )

    # ticker viene como ticker_corto del frontend; resolver underlying
    # para queryar la serie de PreciosAcciones (caso YPFD → YPF).
    underlying = _resolve_underlying(ticker)
    db = get_db_trading()
    col = db["PreciosAcciones"]

    def _serie(t: str) -> list[float]:
        docs = list(col.find(
            {"ticker": t},
            projection={"_id": 0, "fecha": 1, "close": 1},
            sort=[("fecha", 1)],
        ))
        return [d["close"] for d in docs if d.get("close") is not None]

    closes_a = _serie(underlying)
    closes_spy = _serie("SPY")
    closes_qqq = _serie("QQQ")

    if not closes_a:
        return {
            "ticker":          ticker.upper(),
            "last":            None,
            "n_observations":  0,
            "beta":            {"spy": None, "qqq": None},
            "alpha":           {"spy": None, "qqq": None},
            "corr":            {"spy": None, "qqq": None},
            "vol":             {"d30": None, "d60": None},
        }

    rets_a   = returns_from_prices(closes_a)
    rets_spy = returns_from_prices(closes_spy)
    rets_qqq = returns_from_prices(closes_qqq)

    # Truncar al window — usar últimos N puntos comunes.
    n = min(len(rets_a), len(rets_spy), len(rets_qqq), window)
    a_w   = rets_a[-n:]
    spy_w = rets_spy[-n:]
    qqq_w = rets_qqq[-n:]

    ba_spy = beta_alpha(a_w, spy_w)
    ba_qqq = beta_alpha(a_w, qqq_w)
    corr_spy = correlation(a_w, spy_w)
    corr_qqq = correlation(a_w, qqq_w)

    # Vol realizada 30d y 60d (ventana propia, sin truncar al benchmark).
    vol_30 = realized_vol(rets_a[-30:]) if len(rets_a) >= 30 else None
    vol_60 = realized_vol(rets_a[-60:]) if len(rets_a) >= 60 else None

    # Z-score del retorno de hoy vs los días previos en la ventana.
    z_30 = zscore_last(rets_a[-30:]) if len(rets_a) >= 30 else None
    z_60 = zscore_last(rets_a[-60:]) if len(rets_a) >= 60 else None

    return {
        "ticker":         ticker.upper(),
        "last":           closes_a[-1] if closes_a else None,
        "n_observations": n,
        "beta":   {"spy": ba_spy["beta"],  "qqq": ba_qqq["beta"]},
        "alpha":  {"spy": ba_spy["alpha"], "qqq": ba_qqq["alpha"]},
        "corr":   {"spy": corr_spy,        "qqq": corr_qqq},
        "vol":    {"d30": vol_30,          "d60": vol_60},
        "zscore": {"d30": z_30,            "d60": z_60},
    }


@cached(ttl=2)
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
    # Le pasamos el master entero para que pueda resolver underlying ≠
    # ticker_corto cuando aplica (caso YPFD CEDEAR → YPF ADR).
    adr_metrics = _adr_metrics_para_todos(master)

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
        bid    = float(s.get("bid")    or 0)
        offer  = float(s.get("offer")  or 0)
        spread = float(s.get("spread") or 0)
        vwap   = float(s.get("vwap")   or 0)
        volume = float(s.get("volume") or 0)
        spread_pct = (spread / ((bid + offer) / 2) * 100) if (bid > 0 and offer > 0) else None

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
            "nombre":           m.get("nombre"),
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
            # Datos de trading (live desde el motor): puntas, spread, VWAP, VOL.
            "bid":              bid if bid > 0 else None,
            "offer":            offer if offer > 0 else None,
            "spread":           spread if spread > 0 else None,
            "spread_pct":       spread_pct,
            "vwap":             vwap if vwap > 0 else None,
            "volume":           volume if volume > 0 else None,
            # ADR (USD EOD del underlying)
            "adr_last":         adr.get("adr_last"),
            "adr_fecha":        adr.get("adr_fecha"),
            "adr_intraday":     adr.get("adr_intraday"),
            "adr_vs_1d_pct":    adr.get("adr_vs_1d_pct"),
            "adr_ret_7d_pct":   adr.get("adr_ret_7d_pct"),
            "adr_ret_mtd_pct":  adr.get("adr_ret_mtd_pct"),
            "adr_ret_ytd_pct":  adr.get("adr_ret_ytd_pct"),
            "updated_at":       s.get("updated_at"),
        })
    return out
