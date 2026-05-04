"""MM Microstructure — port pragmático de Cartea/Jaimungal/Penalva caps 1-4 a la app.

Service puro (sin FastAPI, sin HTTP). Lee de:
  - Trading.OrderBookL2  (TS Collection, append-only, escrita por engines/order_book_l2.py)
  - Trading.TimeSales    (trades raw, motor_rofex)
  - Trading.MarketSnapshot (último snapshot consolidado, fallback)

No escribe a Mongo. Cache via @cached para los agregados costosos.

Contenido por capítulo:
  cap 1  — book_metrics(snapshot) → mid, microprice, OBI, quoted spread.
  cap 4  — enrich_trade(trade, book) → effective spread, Lee-Ready, walking flag.
  cap 4  — intraday_buckets(fecha) → 1-min agg: NOF, qES, vol realizada, walking %.
  cap 4  — estimate_impact(desde, hasta) → b (permanent) y k (temporary) por OLS.
  cap 4  — smile(dias) → buckets de 30-min × N días, forma U.
  cap 3  — stylized_facts(ventana_dias) → kurtosis, skew, ACF lag-1, ACF|r| persist, JB.

Tres niveles de uso (mismo modelo de datos):
  - Live (1Hz refresh natural del motor): book_metrics + last_trade enriquecido.
  - Tape window (segundos): trades_window enriquecidos con effective spread.
  - Offline (~5min cache): intraday, smile, impact, stylized facts.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from api.cache import cached
from core.mongo import get_mongo_client_read

DB_TRADING = "Trading"
COL_OB = "OrderBookL2"
COL_TS = "TimeSales"
COL_MS = "MarketSnapshot"

# Default — el único ticker capturado en OrderBookL2 hoy. Si en el futuro
# config.TICKERS_BOOK_FULL crece, los endpoints aceptan ticker como param.
DEFAULT_TICKER = "MERV - XMEV - AL30 - CI"


# ─────────────────────────────────────────────────────────────────────────────
# Cap 1 — Book metrics (puras, input = 1 snapshot del book)
# ─────────────────────────────────────────────────────────────────────────────


def book_metrics(snapshot: dict) -> dict[str, float | None]:
    """Las 4 métricas del cap 1, calculadas sobre el top of book.

    snapshot = {"bids": [{"price","size"}, ...], "offers": [...]}.

    Devuelve None en cada campo si falta data (book vacío o un solo lado).
    """
    bids = snapshot.get("bids") or []
    offers = snapshot.get("offers") or []
    if not bids or not offers:
        return _empty_metrics()
    try:
        best_bid = float(bids[0].get("price") or 0)
        bid_size = float(bids[0].get("size") or 0)
        best_ask = float(offers[0].get("price") or 0)
        ask_size = float(offers[0].get("size") or 0)
    except (TypeError, ValueError):
        return _empty_metrics()
    if best_bid <= 0 or best_ask <= 0 or best_ask <= best_bid:
        return _empty_metrics()

    mid = (best_bid + best_ask) / 2.0
    qs = best_ask - best_bid
    qs_bps = (qs / mid) * 10_000 if mid > 0 else None

    sum_top = bid_size + ask_size
    obi = (bid_size - ask_size) / sum_top if sum_top > 0 else 0.0
    micro = mid + (obi / 2.0) * qs

    return {
        "best_bid":      round(best_bid, 4),
        "best_ask":      round(best_ask, 4),
        "bid_size":      bid_size,
        "ask_size":      ask_size,
        "mid":           round(mid, 4),
        "microprice":    round(micro, 4),
        "obi":           round(obi, 4),
        "quoted_spread": round(qs, 4),
        "qs_bps":        round(qs_bps, 2) if qs_bps is not None else None,
    }


def _empty_metrics() -> dict[str, float | None]:
    return {
        "best_bid": None, "best_ask": None,
        "bid_size": None, "ask_size": None,
        "mid": None, "microprice": None,
        "obi": None, "quoted_spread": None, "qs_bps": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Cap 4 — Trade enrichment (puras, input = trade + book del momento)
# ─────────────────────────────────────────────────────────────────────────────


def lee_ready_side(trade_price: float, mid: float | None) -> str:
    """Algoritmo Lee-Ready (1991): clasifica un trade como buy MO / sell MO
    según la posición relativa al mid. "MID" cuando ejecuta justo en el mid
    (caso ambiguo, requeriría tick rule sobre el trade anterior).
    """
    if mid is None or trade_price is None:
        return "UNKNOWN"
    if trade_price > mid:
        return "BUY"
    if trade_price < mid:
        return "SELL"
    return "MID"


def enrich_trade(trade: dict, book: dict | None) -> dict:
    """Enriquece un trade con effective spread, Lee-Ready y walking flag.

    Si `book` es None (no hay snapshot del book en ese instante), devuelve
    el trade con los nuevos campos en None — no descartamos el trade.
    """
    out = dict(trade)
    if book is None:
        out.update({
            "es": None, "es_bps": None,
            "lee_ready": "UNKNOWN", "lee_ready_matches_side": None,
            "walking": None, "mid": None,
        })
        return out

    m = book_metrics(book)
    mid = m["mid"]
    price = float(trade.get("price") or 0)
    size = float(trade.get("size") or 0)
    side = str(trade.get("side") or "").upper()

    if mid is None or price <= 0:
        es = None
        es_bps = None
    else:
        es = abs(price - mid)
        es_bps = (es / mid) * 10_000 if mid > 0 else None

    lr = lee_ready_side(price, mid)
    matches_side = (lr == side) if (side in ("BUY", "SELL") and lr in ("BUY", "SELL")) else None

    if side == "BUY":
        top_size = float((book.get("offers") or [{}])[0].get("size") or 0)
    elif side == "SELL":
        top_size = float((book.get("bids") or [{}])[0].get("size") or 0)
    else:
        top_size = 0.0
    walking = (size > top_size) if top_size > 0 else None

    out.update({
        "es":                     round(es, 4) if es is not None else None,
        "es_bps":                 round(es_bps, 2) if es_bps is not None else None,
        "lee_ready":              lr,
        "lee_ready_matches_side": matches_side,
        "walking":                walking,
        "top_size_at_trade":      top_size if top_size > 0 else None,
        "mid":                    mid,
    })
    return out


def align_trades_to_book(
    trades: list[dict], book_snapshots: list[dict],
) -> list[dict]:
    """Two-pointer sweep: para cada trade, el último snapshot del book con
    `ts <= trade.timestamp`. O(n+m), no O(n·m).

    Asume: ambas listas vienen ordenadas ascendentemente por timestamp.
    """
    if not book_snapshots:
        return [enrich_trade(t, None) for t in trades]

    # Paso 1: índice de timestamps (datetimes) para bisect_right.
    book_ts = [b["ts"] for b in book_snapshots]
    out = []
    for t in trades:
        t_ts = t.get("timestamp")
        if t_ts is None:
            out.append(enrich_trade(t, None))
            continue
        idx = bisect_right(book_ts, t_ts) - 1
        book = book_snapshots[idx] if idx >= 0 else None
        out.append(enrich_trade(t, book))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Query helpers — Mongo
# ─────────────────────────────────────────────────────────────────────────────


def latest_book(ticker: str = DEFAULT_TICKER) -> dict | None:
    """Último doc de OrderBookL2 para `ticker`. None si vacío."""
    db = get_mongo_client_read()[DB_TRADING]
    doc = db[COL_OB].find_one(
        {"ticker": ticker},
        {"_id": 0, "ts": 1, "bids": 1, "offers": 1},
        sort=[("ts", -1)],
    )
    return doc


def book_window(
    ticker: str, desde: datetime, hasta: datetime,
) -> list[dict]:
    """Snapshots del book en [desde, hasta], ordenados ascendente por ts.

    PyMongo deserializa BSON datetimes como naive por default. Normalizamos
    a UTC tz-aware acá para que align_trades_to_book pueda comparar contra
    los timestamps de los trades (que ya vienen tz-aware desde trades_window).
    """
    db = get_mongo_client_read()[DB_TRADING]
    cursor = (
        db[COL_OB]
        .find(
            {"ticker": ticker, "ts": {"$gte": desde, "$lte": hasta}},
            {"_id": 0, "ts": 1, "bids": 1, "offers": 1},
        )
        .sort("ts", 1)
    )
    out = []
    for doc in cursor:
        ts = doc.get("ts")
        if isinstance(ts, datetime) and ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        out.append({"ts": ts, "bids": doc.get("bids") or [], "offers": doc.get("offers") or []})
    return out


def _to_naive_art(dt: datetime) -> datetime:
    """UTC tz-aware (o naive UTC) → naive ART, formato que usa TimeSales."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return (dt.astimezone(UTC) - timedelta(hours=3)).replace(tzinfo=None)


def _to_aware_utc(dt_naive_art: datetime) -> datetime:
    """Naive ART → UTC tz-aware. Inverso de _to_naive_art."""
    return (dt_naive_art + timedelta(hours=3)).replace(tzinfo=UTC)


def trades_window(
    ticker: str, desde: datetime, hasta: datetime, dust: int = 0,
) -> list[dict]:
    """Trades del ticker en [desde, hasta], ordenados ascendente por timestamp.

    `desde` y `hasta` se reciben en UTC tz-aware (o naive UTC) — internamente
    se convierten a naive ART para queryar TimeSales (donde el timestamp se
    guarda naive ART por motor_rofex). Los timestamps en el output se
    devuelven como UTC tz-aware (alineados con OrderBookL2).
    """
    desde_q = _to_naive_art(desde)
    hasta_q = _to_naive_art(hasta)

    db = get_mongo_client_read()[DB_TRADING]
    cursor = (
        db[COL_TS]
        .find(
            {
                "ticker":    ticker,
                "timestamp": {"$gte": desde_q, "$lte": hasta_q},
                "size":      {"$gte": dust},
            },
            {"_id": 0, "timestamp": 1, "price": 1, "size": 1, "side": 1},
        )
        .sort("timestamp", 1)
    )
    out = []
    for doc in cursor:
        ts = doc.get("timestamp")
        if isinstance(ts, datetime) and ts.tzinfo is None:
            ts = _to_aware_utc(ts)
        out.append({
            "timestamp": ts,
            "price":     float(doc.get("price") or 0),
            "size":      float(doc.get("size") or 0),
            "side":      str(doc.get("side") or "").upper(),
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints level-1: live + tape (sin cache, datos frescos)
# ─────────────────────────────────────────────────────────────────────────────


def get_live(ticker: str = DEFAULT_TICKER) -> dict[str, Any]:
    """Snapshot completo del estado actual del ticker.

    Combina:
      - último book de OrderBookL2 → métricas cap 1.
      - último trade de TimeSales (24h atrás como ventana defensiva).

    Pensado para refresh ~1Hz desde el frontend.
    """
    book = latest_book(ticker)
    if book is None:
        return {
            "ticker":  ticker,
            "ts_book": None,
            "book":    {"bids": [], "offers": []},
            "metrics": _empty_metrics(),
            "last_trade": None,
        }

    metrics = book_metrics(book)

    # Último trade de las últimas 24h (defensivo en caso de mercado cerrado).
    # TimeSales guarda naive ART; queryamos en ese formato.
    db = get_mongo_client_read()[DB_TRADING]
    desde_q = _to_naive_art(datetime.now(UTC) - timedelta(hours=24))
    last_trade_doc = db[COL_TS].find_one(
        {"ticker": ticker, "timestamp": {"$gte": desde_q}},
        {"_id": 0, "timestamp": 1, "price": 1, "size": 1, "side": 1},
        sort=[("timestamp", -1)],
    )

    last_trade = None
    if last_trade_doc:
        ts_t = last_trade_doc.get("timestamp")
        if isinstance(ts_t, datetime) and ts_t.tzinfo is None:
            ts_t = _to_aware_utc(ts_t)
        last_trade = enrich_trade(
            {
                "timestamp": ts_t,
                "price":     float(last_trade_doc.get("price") or 0),
                "size":      float(last_trade_doc.get("size") or 0),
                "side":      str(last_trade_doc.get("side") or "").upper(),
            },
            book,
        )

    return {
        "ticker":   ticker,
        "ts_book":  book.get("ts").isoformat() if isinstance(book.get("ts"), datetime) else book.get("ts"),
        "book":     {"bids": book.get("bids") or [], "offers": book.get("offers") or []},
        "metrics":  metrics,
        "last_trade": last_trade,
    }


def get_tape(
    ticker: str = DEFAULT_TICKER,
    desde: str | None = None,
    hasta: str | None = None,
    ventana_min: int = 30,
    limit: int = 500,
) -> dict[str, Any]:
    """Trades de la ventana, cada uno enriquecido con cap 4 (ES, Lee-Ready, walking).

    Si `desde` / `hasta` no vienen, usa los últimos `ventana_min` minutos.
    Hard cap `limit` trades para no romper el frontend.
    """
    desde_dt, hasta_dt = _resolve_window(desde, hasta, ventana_min)

    trades = trades_window(ticker, desde_dt, hasta_dt)
    books = book_window(ticker, desde_dt, hasta_dt)
    enriched = align_trades_to_book(trades, books)

    if len(enriched) > limit:
        enriched = enriched[-limit:]

    # Serialización: timestamps a iso.
    for t in enriched:
        ts = t.get("timestamp")
        if isinstance(ts, datetime):
            t["timestamp"] = ts.isoformat()

    return {
        "ticker": ticker,
        "desde":  desde_dt.isoformat(),
        "hasta":  hasta_dt.isoformat(),
        "n":      len(enriched),
        "trades": enriched,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints level-2: agregados intradía (cap 4) + smile + impact + sf (cap 3)
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=300)
def get_intraday(
    ticker: str = DEFAULT_TICKER,
    fecha: str | None = None,
    bucket_min: int = 1,
) -> dict[str, Any]:
    """Buckets de N-min sobre todo un día con métricas cap 4 agregadas.

    Por bucket: NOF, qES, walking %, vol realizada (sobre mid), volumen,
    n trades. Output ordenado cronológicamente.
    """
    fecha_d = _parse_date(fecha) if fecha else _last_trading_date(ticker)
    if fecha_d is None:
        return {"ticker": ticker, "fecha": None, "bucket_min": bucket_min, "buckets": []}

    # `fecha` es la fecha de mercado ART. Convertimos los bordes a UTC
    # tz-aware (que es lo que ambas queries esperan internamente).
    desde_art = datetime.combine(fecha_d, time.min)
    hasta_art = datetime.combine(fecha_d, time.max)
    desde_dt = _to_aware_utc(desde_art)
    hasta_dt = _to_aware_utc(hasta_art)

    trades = trades_window(ticker, desde_dt, hasta_dt)
    books = book_window(ticker, desde_dt, hasta_dt)
    enriched = align_trades_to_book(trades, books)

    if not enriched:
        return {
            "ticker": ticker, "fecha": fecha_d.isoformat(),
            "bucket_min": bucket_min, "buckets": [],
        }

    # Buckets por bucket_min minutos.
    bucket_seconds = bucket_min * 60
    by_bucket: dict[int, list[dict]] = {}
    for t in enriched:
        ts = t.get("timestamp")
        if not isinstance(ts, datetime):
            continue
        # Anclamos al inicio del día UTC, expresado en segundos.
        delta_s = int((ts - desde_dt).total_seconds())
        bucket_idx = delta_s // bucket_seconds
        by_bucket.setdefault(bucket_idx, []).append(t)

    buckets_out = []
    for idx in sorted(by_bucket.keys()):
        bucket_trades = by_bucket[idx]
        bucket_start = desde_dt + timedelta(seconds=idx * bucket_seconds)
        agg = _aggregate_bucket(bucket_trades)
        agg["ts_start"] = bucket_start.isoformat()
        agg["ts_start_ar"] = (bucket_start - timedelta(hours=3)).strftime("%H:%M")
        buckets_out.append(agg)

    return {
        "ticker":     ticker,
        "fecha":      fecha_d.isoformat(),
        "bucket_min": bucket_min,
        "buckets":    buckets_out,
        "n_trades":   len(enriched),
    }


def _aggregate_bucket(trades: list[dict]) -> dict[str, Any]:
    """Agregaciones cap 4 sobre los trades de un bucket."""
    n = len(trades)
    volume = sum(t.get("size") or 0 for t in trades)

    # NOF (Lee-Ready): buy_size − sell_size.
    nof = 0.0
    for t in trades:
        sz = t.get("size") or 0
        if t.get("lee_ready") == "BUY":
            nof += sz
        elif t.get("lee_ready") == "SELL":
            nof -= sz

    # qES = quantity-weighted effective spread.
    es_data = [(t["es"], t["size"]) for t in trades if t.get("es") is not None and (t.get("size") or 0) > 0]
    qES = (
        sum(es * sz for es, sz in es_data) / sum(sz for _, sz in es_data)
        if es_data else None
    )

    # Walking incidence.
    walking_count = sum(1 for t in trades if t.get("walking") is True)
    walking_pct = walking_count / n if n else None

    # Vol realizada del bucket sobre mid: stdev de retornos a 1-trade.
    mids = [t["mid"] for t in trades if t.get("mid")]
    rv = None
    if len(mids) >= 3:
        returns = [
            (mids[i] - mids[i - 1]) / mids[i - 1]
            for i in range(1, len(mids))
            if mids[i - 1] > 0
        ]
        if len(returns) >= 2:
            rv = _stdev(returns)

    # mid de cierre del bucket (último mid observado).
    last_mid = next((t["mid"] for t in reversed(trades) if t.get("mid")), None)

    return {
        "n_trades":      n,
        "volume":        round(volume, 2),
        "nof":           round(nof, 2),
        "qES":           round(qES, 4) if qES is not None else None,
        "walking_pct":   round(walking_pct * 100, 2) if walking_pct is not None else None,
        "realized_vol":  round(rv * 10_000, 2) if rv is not None else None,  # bps
        "mid_close":     last_mid,
    }


@cached(ttl=600)
def get_impact(
    ticker: str = DEFAULT_TICKER,
    desde: str | None = None,
    hasta: str | None = None,
    dias: int = 5,
    bucket_min: int = 1,
) -> dict[str, Any]:
    """Estimación empírica del cap 4:

    - **Permanent impact b**: ΔS_n = b · π_n + ε  (Δmid del bucket vs NOF del bucket).
    - **Temporary impact k**: |trade.price − mid_at_trade| = k · Q + ε  (per-trade).

    Por simplicidad usa OLS plano + winsorización 1%. Pendiente: Huber/RLM.
    """
    fechas = _resolve_dias(desde, hasta, dias)

    # Combinar buckets de todos los días para la regresión b.
    bucket_pairs: list[tuple[float, float]] = []  # (NOF, Δmid)
    trade_pairs: list[tuple[float, float]] = []   # (size, |slippage|)

    for f in fechas:
        intraday = get_intraday(ticker=ticker, fecha=f.isoformat(), bucket_min=bucket_min)
        prev_mid = None
        for b in intraday["buckets"]:
            mid_close = b.get("mid_close")
            nof = b.get("nof")
            if mid_close is not None and prev_mid is not None and nof is not None:
                d_mid = mid_close - prev_mid
                bucket_pairs.append((float(nof), float(d_mid)))
            if mid_close is not None:
                prev_mid = mid_close

        # Para `k`: vamos al tape del día completo.
        desde_dt = datetime.combine(f, time.min, tzinfo=UTC)
        hasta_dt = datetime.combine(f, time.max, tzinfo=UTC)
        trades = trades_window(ticker, desde_dt, hasta_dt)
        books = book_window(ticker, desde_dt, hasta_dt)
        enriched = align_trades_to_book(trades, books)
        for t in enriched:
            sz = t.get("size") or 0
            es = t.get("es")
            if sz > 0 and es is not None:
                trade_pairs.append((float(sz), float(es)))

    bucket_pairs = _winsorize_pairs(bucket_pairs, 0.01)
    trade_pairs = _winsorize_pairs(trade_pairs, 0.01)

    b, b_r2, b_n = _ols(bucket_pairs)
    k, k_r2, k_n = _ols(trade_pairs)

    return {
        "ticker":     ticker,
        "desde":      fechas[0].isoformat() if fechas else None,
        "hasta":      fechas[-1].isoformat() if fechas else None,
        "dias":       len(fechas),
        "permanent_impact": {
            "b":           round(b, 6) if b is not None else None,
            "r2":          round(b_r2, 4) if b_r2 is not None else None,
            "n_buckets":   b_n,
            "interpretacion": (
                "Δmid esperado por unidad de NOF (signed VN). "
                "Mayor b → mercado menos líquido / más informacional."
            ),
        },
        "temporary_impact": {
            "k":           round(k, 6) if k is not None else None,
            "r2":          round(k_r2, 4) if k_r2 is not None else None,
            "n_trades":    k_n,
            "interpretacion": (
                "Slippage |price - mid| esperado por unidad de tamaño. "
                "Mayor k → menor profundidad disponible."
            ),
        },
    }


@cached(ttl=600)
def get_smile(
    ticker: str = DEFAULT_TICKER,
    dias: int = 5,
    bucket_min: int = 30,
) -> dict[str, Any]:
    """Smile intradiario: promedio de volumen + vol realizada por bucket de
    `bucket_min` minutos sobre los últimos N días hábiles.

    Esperás forma de U: pico apertura, mínimo mediodía, pico mayor cierre.
    """
    fechas = _resolve_dias(None, None, dias)
    if not fechas:
        return {"ticker": ticker, "dias": 0, "buckets": []}

    # Acumulador por bucket_idx (mismo idx para todos los días).
    by_idx: dict[int, list[dict]] = {}
    for f in fechas:
        day = get_intraday(ticker=ticker, fecha=f.isoformat(), bucket_min=bucket_min)
        for b in day["buckets"]:
            try:
                ts = datetime.fromisoformat(b["ts_start"])
            except ValueError:
                continue
            day_start = ts.replace(hour=0, minute=0, second=0, microsecond=0)
            idx = int((ts - day_start).total_seconds()) // (bucket_min * 60)
            by_idx.setdefault(idx, []).append(b)

    out_buckets = []
    for idx in sorted(by_idx.keys()):
        items = by_idx[idx]
        n_dias = len(items)
        avg_vol = (
            sum(it["volume"] for it in items if it.get("volume") is not None) / n_dias
            if n_dias else None
        )
        rvs = [it["realized_vol"] for it in items if it.get("realized_vol") is not None]
        avg_rv = sum(rvs) / len(rvs) if rvs else None
        avg_n = (
            sum(it["n_trades"] for it in items if it.get("n_trades") is not None) / n_dias
            if n_dias else None
        )
        # Hora ART del bucket.
        ts_first = datetime.fromisoformat(items[0]["ts_start"])
        ts_ar = (ts_first - timedelta(hours=3)).strftime("%H:%M")

        out_buckets.append({
            "idx":              idx,
            "ts_ar":            ts_ar,
            "n_dias_obs":       n_dias,
            "avg_volume":       round(avg_vol, 2) if avg_vol is not None else None,
            "avg_realized_vol": round(avg_rv, 2) if avg_rv is not None else None,
            "avg_n_trades":     round(avg_n, 2) if avg_n is not None else None,
        })

    return {
        "ticker":     ticker,
        "dias":       len(fechas),
        "bucket_min": bucket_min,
        "buckets":    out_buckets,
    }


@cached(ttl=600)
def get_stylized_facts(
    ticker: str = DEFAULT_TICKER,
    desde: str | None = None,
    hasta: str | None = None,
    dias: int = 5,
    bucket_min: int = 1,
) -> dict[str, Any]:
    """Cap 3: kurtosis, skewness, ACF lag-1 (mid vs last), persistencia ACF|r|, JB.

    - Retornos sobre mid de cada bucket (~1 min) → ACF(1) debería ser cercana a 0.
    - Retornos sobre last_price del bucket → ACF(1) debería ser negativa (bid-ask bounce).
    - Persistencia: ACF(k) sobre |r| para k=1..20.
    - Jarque-Bera con p-value vs χ²(2).
    """
    fechas = _resolve_dias(desde, hasta, dias)

    mids: list[float] = []
    lasts: list[float] = []

    for f in fechas:
        intraday = get_intraday(ticker=ticker, fecha=f.isoformat(), bucket_min=bucket_min)
        for b in intraday["buckets"]:
            m = b.get("mid_close")
            if m and m > 0:
                mids.append(float(m))
                lasts.append(float(m))   # placeholder — TODO: capturar last del bucket

    if len(mids) < 30:
        return {
            "ticker": ticker, "dias": len(fechas),
            "n_obs": len(mids),
            "error": "menos de 30 observaciones — extender ventana",
        }

    r_mid = _returns(mids)
    r_last = _returns(lasts)

    # Stats sobre r_mid (es lo que usa Cartea — sin bid-ask bounce).
    n = len(r_mid)
    mu = sum(r_mid) / n
    sigma = _stdev(r_mid)
    skew = _moment_n(r_mid, mu, sigma, 3)
    kurt = _moment_n(r_mid, mu, sigma, 4)

    acf1_mid = _acf_lag(r_mid, 1)
    acf1_last = _acf_lag(r_last, 1)

    abs_r = [abs(r) for r in r_mid]
    acf_abs = [_acf_lag(abs_r, k) for k in range(1, 21)]
    persistence = sum(1 for v in acf_abs if v is not None and v > 0.05)

    # Jarque-Bera.
    jb = (n / 6.0) * (skew ** 2 + ((kurt - 3.0) ** 2) / 4.0)
    jb_p = _chi2_2_pvalue(jb)

    return {
        "ticker":      ticker,
        "dias":        len(fechas),
        "bucket_min":  bucket_min,
        "n_obs":       n,
        "mu":          round(mu * 10_000, 4),       # bps
        "sigma":       round(sigma * 10_000, 4),    # bps
        "skewness":    round(skew, 4),
        "kurtosis":    round(kurt, 4),
        "exceso_kurt": round(kurt - 3.0, 4),
        "acf1_mid":    round(acf1_mid, 4) if acf1_mid is not None else None,
        "acf1_last":   round(acf1_last, 4) if acf1_last is not None else None,
        "acf_abs_persistencia": persistence,
        "acf_abs":     [round(v, 4) if v is not None else None for v in acf_abs],
        "jarque_bera": round(jb, 2),
        "jb_p":        round(jb_p, 6),
        "interpretacion": _interpret_sf(skew, kurt, acf1_mid, acf1_last, persistence, jb_p),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_window(
    desde: str | None, hasta: str | None, ventana_min: int,
) -> tuple[datetime, datetime]:
    if hasta:
        hasta_dt = datetime.fromisoformat(hasta).replace(tzinfo=UTC)
    else:
        hasta_dt = datetime.now(UTC)
    if desde:
        desde_dt = datetime.fromisoformat(desde).replace(tzinfo=UTC)
    else:
        desde_dt = hasta_dt - timedelta(minutes=ventana_min)
    return desde_dt, hasta_dt


def _parse_date(s: str) -> date | None:
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _last_trading_date(ticker: str) -> date | None:
    """Última fecha (ART) con trades en TimeSales para `ticker`."""
    db = get_mongo_client_read()[DB_TRADING]
    doc = db[COL_TS].find_one(
        {"ticker": ticker}, {"_id": 0, "timestamp": 1}, sort=[("timestamp", -1)],
    )
    if not doc:
        return None
    ts = doc.get("timestamp")
    if isinstance(ts, datetime):
        # ts viene naive ART; .date() es la fecha de mercado ART.
        return ts.date()
    return None


def _resolve_dias(
    desde: str | None, hasta: str | None, dias: int,
) -> list[date]:
    """Días hábiles dentro del rango. Si `dias` es N, los últimos N días con
    actividad (>=20 trades) en TimeSales.
    """
    if desde and hasta:
        d0 = _parse_date(desde)
        d1 = _parse_date(hasta)
        if not d0 or not d1:
            return []
        out = []
        cur = d0
        while cur <= d1:
            if cur.weekday() < 5:
                out.append(cur)
            cur = cur + timedelta(days=1)
        return out

    # Modo "últimos N días con actividad". TimeSales tiene timestamps naive ART.
    db = get_mongo_client_read()[DB_TRADING]
    desde_q = _to_naive_art(datetime.now(UTC) - timedelta(days=max(dias * 3, 30)))
    pipeline = [
        {"$match": {"timestamp": {"$gte": desde_q}}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
            "n":   {"$sum": 1},
        }},
        {"$match": {"n": {"$gte": 20}}},
        {"$sort": {"_id": -1}},
        {"$limit": dias},
    ]
    fechas = []
    for r in db[COL_TS].aggregate(pipeline):
        d = _parse_date(r["_id"])
        if d:
            fechas.append(d)
    fechas.sort()
    return fechas


def _returns(prices: list[float]) -> list[float]:
    out = []
    for i in range(1, len(prices)):
        if prices[i - 1] > 0:
            out.append((prices[i] - prices[i - 1]) / prices[i - 1])
    return out


def _stdev(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mu = sum(xs) / n
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1))


def _moment_n(xs: list[float], mu: float, sigma: float, n_pow: int) -> float:
    n = len(xs)
    if n == 0 or sigma == 0:
        return 0.0
    return sum((x - mu) ** n_pow for x in xs) / (n * (sigma ** n_pow))


def _acf_lag(xs: list[float], k: int) -> float | None:
    n = len(xs)
    if n <= k + 1:
        return None
    mu = sum(xs) / n
    var = sum((x - mu) ** 2 for x in xs) / n
    if var == 0:
        return None
    cov = sum((xs[i] - mu) * (xs[i - k] - mu) for i in range(k, n)) / n
    return cov / var


def _ols(pairs: list[tuple[float, float]]) -> tuple[float | None, float | None, int]:
    """OLS sin intercepto: y = β·x + ε. Devuelve (β, R², n).

    Sin intercepto porque conceptualmente esperamos β=0 cuando x=0 (cap 4
    asume ε con media 0). R² = 1 − Σ(y - β·x)² / Σ(y - ȳ)².
    """
    if len(pairs) < 5:
        return None, None, len(pairs)
    sxx = sum(x * x for x, _ in pairs)
    sxy = sum(x * y for x, y in pairs)
    if sxx == 0:
        return 0.0, 0.0, len(pairs)
    beta = sxy / sxx
    yb = sum(y for _, y in pairs) / len(pairs)
    ss_tot = sum((y - yb) ** 2 for _, y in pairs)
    ss_res = sum((y - beta * x) ** 2 for x, y in pairs)
    r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    return beta, r2, len(pairs)


def _winsorize_pairs(
    pairs: list[tuple[float, float]], pct: float,
) -> list[tuple[float, float]]:
    """Winsoriza separadamente las dos columnas a percentil [pct, 1-pct]."""
    if len(pairs) < 20:
        return pairs
    xs = sorted(p[0] for p in pairs)
    ys = sorted(p[1] for p in pairs)
    lo_x = xs[int(len(xs) * pct)]
    hi_x = xs[int(len(xs) * (1 - pct))]
    lo_y = ys[int(len(ys) * pct)]
    hi_y = ys[int(len(ys) * (1 - pct))]
    return [
        (max(lo_x, min(hi_x, x)), max(lo_y, min(hi_y, y)))
        for x, y in pairs
    ]


def _chi2_2_pvalue(jb: float) -> float:
    """p-value de χ²(2) — la distribución es exponencial: P(X > x) = e^(-x/2)."""
    if jb < 0:
        return 1.0
    return math.exp(-jb / 2.0)


def _interpret_sf(
    skew: float, kurt: float,
    acf1_mid: float | None, acf1_last: float | None,
    persistence: int, jb_p: float,
) -> list[str]:
    msgs = []
    if abs(skew) > 0.5:
        msgs.append(
            f"Skewness {skew:.2f} → "
            f"asimetría {'negativa (cola izquierda más larga)' if skew < 0 else 'positiva'}."
        )
    if kurt > 4:
        msgs.append(f"Kurtosis {kurt:.2f} > 3 → colas pesadas (no normalidad).")
    if jb_p < 0.05:
        msgs.append(f"Jarque-Bera p={jb_p:.4f} → rechaza normalidad al 5%.")
    if acf1_last is not None and acf1_last < -0.05:
        msgs.append(f"ACF(1) sobre last = {acf1_last:.3f} → bid-ask bounce presente.")
    if acf1_mid is not None and abs(acf1_mid) < 0.05:
        msgs.append(f"ACF(1) sobre mid = {acf1_mid:.3f} → mercado eficiente direccionalmente.")
    if persistence >= 5:
        msgs.append(
            f"ACF de |r| persistente ({persistence}/20 lags > 0.05) → volatility clustering."
        )
    if not msgs:
        msgs.append("Sin desviaciones significativas en esta ventana.")
    return msgs
