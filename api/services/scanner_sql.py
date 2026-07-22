"""api/services/scanner_sql.py — vista Scanner (Renta Variable) leyendo Postgres.

Espejo SQL-native de `api/services/scanner.py`. MISMO shape de salida que el
path Mongo — el frontend no distingue. Fuentes:

  - `mercado.cedears`           ← Trading.Cedears (master, passthrough jsonb)
  - `mercado.cedears_snapshot`  ← Trading.CedearsSnapshot (live ARS, jsonb)
  - `mercado.adr_snapshot`      ← Trading.AdrSnapshot (live USD del underlying, jsonb)
  - `mercado.precios_acciones`  ← Trading.PreciosAcciones (EOD del underlying, columnar)
  - `mercado.day_trading_stats` ← Trading.DayTradingStats (costumbre intradía, jsonb)

Paridad de datetime: el jsonb de los snapshots se escribió con datetime→isoformat
(`pg_mirror.doc_iso`) → `updated_at`/`adr_fecha` salen como string ISO, idéntico
a lo que FastAPI emite en el path Mongo (jsonable_encoder usa isoformat).

`get_ccl_live` NO se reimplementa acá: es el dominio DÓLAR (live-only, snapshot
intradía + cierre previo) y vive en `api/services/scanner.py` (SQL-native, sobre
`core.dolar_sql`). Se reexporta desde allá para no duplicar el cálculo de retorno USD;
lo mismo con el tape intradía (`get_cedears_trades` / `get_cedears_intraday`).

El dual-run (flag `SCANNER_SQL` + selector `_scanner()` en el router) se ELIMINÓ: tras
el decomiso de Mongo ambas ramas terminaban acá. El router invoca este módulo directo.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from psycopg.rows import dict_row

from api.cache import cached
from api.services.scanner import get_ccl_live  # dolar live (Mongo) — reexport para el router
from core.postgres import get_pool

__all__ = [
    "get_ccl_live",
    "get_cedears_intraday",
    "get_cedears_scanner",
    "get_cedears_trades",
    "get_pivot_points",
    "get_quant_stats",
    "get_ticker_returns",
    "get_universo",
]


# ── helpers ──────────────────────────────────────────────────────────────────
def _master_activos() -> list[dict]:
    """Docs master (activo=true) reconstruidos desde el jsonb — shape idéntico al
    doc Mongo de Trading.Cedears, MÁS la clasificación de negocio que vive en
    columnas materializadas (no en el jsonb): `rubro` (reemplaza `sector`, más
    granular) y `es_ia` (bool, ecosistema IA). Se inyectan en el dict para que el
    scanner los exponga por CEDEAR. NO pisan campos del jsonb — `sector` queda
    intacto por compatibilidad."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT data, rubro, es_ia FROM mercado.cedears WHERE activo IS TRUE")
        out: list[dict] = []
        for r in cur.fetchall():
            d = dict(r["data"] or {})
            d["rubro"] = r["rubro"]
            d["es_ia"] = r["es_ia"]
            out.append(d)
        return out


def _resolve_underlying(ticker_corto: str) -> str:
    """Mapea ticker_corto (BYMA) → underlying (US ticker). Fallback al ticker_corto
    si no hay fila o el campo está vacío. Igual que scanner._resolve_underlying."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT underlying FROM mercado.cedears WHERE ticker_corto = %s LIMIT 1",
            (ticker_corto.upper(),),
        )
        row = cur.fetchone()
    return (row["underlying"] if row else None) or ticker_corto.upper()


def _serie_closes(underlying: str) -> list[float]:
    """Cierres EOD asc por fecha (sin None) de un underlying."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT close FROM mercado.precios_acciones "
            "WHERE ticker = %s AND close IS NOT NULL ORDER BY fecha",
            (underlying,),
        )
        return [float(r[0]) for r in cur.fetchall()]


# ── ADR metrics (USD del underlying) ─────────────────────────────────────────
@cached(ttl=900)
def _eod_anchors_por_underlying(underlyings: tuple[str, ...]) -> dict[str, dict]:
    """Parte EOD (cambia 1×/día) del scanner, separada del live para NO releer la
    historia entera de precios_acciones cada 2s (era ~60k filas por hit del scanner;
    estos valores solo cambian cuando corre el job EOD). Cacheado 15min.

    Por underlying devuelve las anclas/bases derivadas del cierre EOD:
      base_wtd/7d/mtd/ytd: cierre del último EOD ≤ el anchor (para los retornos)
      base_15r: cierre 15 ruedas antes del último EOD (count-based)
      eod_last_close / eod_prev_close: últimos 2 cierres EOD (fallback sin live + vs_1d)
      eod_last_fecha: fecha (ISO) del último EOD · dollar_vol: close×volume del último EOD
    """
    if not underlyings:
        return {}
    hoy = datetime.now(UTC).replace(tzinfo=None)
    anchor_7d = hoy - timedelta(days=7)
    # WTD: lunes 00h de la semana en curso → ancla en el cierre del viernes previo.
    anchor_wtd = (hoy - timedelta(days=hoy.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    anchor_mtd = hoy.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    anchor_ytd = hoy.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    docs_by: dict[str, list[dict]] = {}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT ticker, fecha, close, volume FROM mercado.precios_acciones "
            "WHERE ticker = ANY(%s) ORDER BY ticker, fecha",
            (list(underlyings),),
        )
        for d in cur.fetchall():
            fecha = d["fecha"]
            docs_by.setdefault(d["ticker"], []).append(
                {"fecha": datetime(fecha.year, fecha.month, fecha.day),  # naive 00h
                 "close": float(d["close"]) if d["close"] is not None else None,
                 "volume": float(d["volume"]) if d["volume"] is not None else None})

    def _base_le(docs: list[dict], anchor_ts: datetime) -> float | None:
        """Cierre del EOD más reciente ≤ anchor (== el _ret_vs original, sin last_close)."""
        for d in reversed(docs):
            if d["fecha"] <= anchor_ts:
                base = d.get("close")
                return base if (base and base > 0) else None
        return None

    out: dict[str, dict] = {}
    for u, docs in docs_by.items():
        docs.sort(key=lambda x: x["fecha"])
        ld = docs[-1] if docs else {}
        base15 = docs[-16].get("close") if len(docs) >= 16 else None
        out[u] = {
            "base_wtd": _base_le(docs, anchor_wtd),
            "base_7d":  _base_le(docs, anchor_7d),
            "base_mtd": _base_le(docs, anchor_mtd),
            "base_ytd": _base_le(docs, anchor_ytd),
            "base_15r": base15 if (base15 and base15 > 0) else None,
            "eod_last_close": ld.get("close"),
            "eod_prev_close": docs[-2].get("close") if len(docs) >= 2 else None,
            "eod_last_fecha": ld["fecha"].isoformat() if ld.get("fecha") else None,
            "dollar_vol": (ld["close"] * ld["volume"]
                           if ld.get("close") and ld.get("volume") else None),
        }
    return out


def _adr_metrics_para_todos(master: list[dict]) -> dict[str, dict]:
    """Métricas ADR (USD) por ticker_corto. La parte EOD (anclas de retorno) viene
    cacheada de `_eod_anchors_por_underlying` (15min); acá solo se lee el live
    (mercado.adr_snapshot, ~chico) y se combinan — sin releer los ~60k EOD cada 2s.

    Mismo shape/semántica que antes: el retorno es `last_close (live o EOD) / base_EOD`.
    """
    if not master:
        return {}

    corto_to_underlying: dict[str, str] = {
        m["ticker_corto"]: (m.get("underlying") or m["ticker_corto"])
        for m in master
    }
    underlyings = sorted(set(corto_to_underlying.values()))

    anchors = _eod_anchors_por_underlying(underlyings=tuple(underlyings))

    # Live USD (chico): 1 query a adr_snapshot.
    live_by_underlying: dict[str, dict] = {}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT data FROM mercado.adr_snapshot WHERE ticker = ANY(%s)",
            (underlyings,),
        )
        for r in cur.fetchall():
            d = r["data"]
            if d and d.get("ticker"):
                live_by_underlying[d["ticker"]] = d

    hoy = datetime.now(UTC).replace(tzinfo=None)

    def _live_dt(live: dict):
        ua = live.get("updated_at")
        if isinstance(ua, str):
            try:
                return datetime.fromisoformat(ua)
            except ValueError:
                return None
        return ua if isinstance(ua, datetime) else None

    def _ret(last_close: float | None, base: float | None) -> float | None:
        if last_close is None or not base or base <= 0:
            return None
        return ((last_close / base) - 1) * 100

    out: dict[str, dict] = {}
    for ticker_corto, underlying in corto_to_underlying.items():
        eod = anchors.get(underlying)
        live = live_by_underlying.get(underlying)

        if not eod and not live:
            out[ticker_corto] = {
                "adr_last": None, "adr_fecha": None, "adr_intraday": None,
                "adr_vs_1d_pct": None, "adr_ret_wtd_pct": None, "adr_ret_7d_pct": None,
                "adr_ret_15r_pct": None, "adr_ret_mtd_pct": None, "adr_ret_ytd_pct": None,
                "adr_dollar_vol": None,
            }
            continue
        eod = eod or {}

        last_close: float | None = None
        last_fecha = None
        adr_intraday: bool | None = None
        if live and live.get("c"):
            last_close = float(live["c"])
            ld = _live_dt(live)
            last_fecha = ld.isoformat() if isinstance(ld, datetime) else None
            t = live.get("t")
            if isinstance(t, (int, float)) and t > 0:
                adr_intraday = datetime.fromtimestamp(t, tz=UTC).date() >= hoy.date()
        elif eod.get("eod_last_close") is not None:
            last_close = eod["eod_last_close"]
            last_fecha = eod.get("eod_last_fecha")
            adr_intraday = False

        vs_1d = None
        if live and last_close and live.get("pc"):
            pc = float(live["pc"])
            vs_1d = ((last_close / pc) - 1) * 100 if pc > 0 else None
        elif last_close and eod.get("eod_prev_close"):
            vs_1d = _ret(last_close, eod["eod_prev_close"])

        out[ticker_corto] = {
            "adr_last": last_close,
            "adr_fecha": last_fecha,
            "adr_intraday": adr_intraday,
            "adr_vs_1d_pct": vs_1d,
            "adr_ret_wtd_pct": _ret(last_close, eod.get("base_wtd")),
            "adr_ret_7d_pct": _ret(last_close, eod.get("base_7d")),
            "adr_ret_15r_pct": _ret(last_close, eod.get("base_15r")),
            "adr_ret_mtd_pct": _ret(last_close, eod.get("base_mtd")),
            "adr_ret_ytd_pct": _ret(last_close, eod.get("base_ytd")),
            "adr_dollar_vol": eod.get("dollar_vol"),
        }
    return out


# ── scanner principal ────────────────────────────────────────────────────────
@cached(ttl=2)
def get_cedears_scanner() -> list[dict]:
    """Master + snapshot joined por ticker, con métricas operativas. Shape idéntico
    a scanner.get_cedears_scanner (CEDEAR live ARS + ADR USD EOD)."""
    master = _master_activos()

    snapshots: dict[str, dict] = {}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT data FROM mercado.cedears_snapshot")
        for r in cur.fetchall():
            d = r["data"]
            if d and d.get("ticker"):
                snapshots[d["ticker"]] = d

    adr_metrics = _adr_metrics_para_todos(master)
    ccl_1d = get_ccl_live().get("vs_1d_pct")

    out: list[dict] = []
    for m in master:
        s = snapshots.get(m["ticker"], {})
        last = float(s.get("last") or 0)
        open_ = float(s.get("open") or 0)
        close = float(s.get("close") or 0)
        high = float(s.get("high") or 0)
        low = float(s.get("low") or 0)
        bid = float(s.get("bid") or 0)
        offer = float(s.get("offer") or 0)
        spread = float(s.get("spread") or 0)
        vwap = float(s.get("vwap") or 0)
        volume = float(s.get("volume") or 0)
        total_money = float(s.get("total_money") or 0)
        spread_pct = (spread / ((bid + offer) / 2) * 100) if (bid > 0 and offer > 0) else None

        intraday = ((last / open_) - 1) * 100 if last > 0 and open_ > 0 else None
        vs_1d = ((last / close) - 1) * 100 if last > 0 and close > 0 else None

        vs_1d_usd = None
        if vs_1d is not None and ccl_1d is not None:
            vs_1d_usd = ((1 + vs_1d / 100) / (1 + ccl_1d / 100) - 1) * 100

        adr = adr_metrics.get(m["ticker_corto"], {})
        out.append({
            "ticker_corto": m["ticker_corto"],
            "nombre": m.get("nombre"),
            "underlying": m.get("underlying"),
            "ratio_cedear": m.get("ratio_cedear"),
            "sector": m.get("sector"),
            "rubro": m.get("rubro"),
            "es_ia": m.get("es_ia"),
            "industria": m.get("industria"),
            "region": m.get("region"),
            "pais": m.get("pais"),
            "last": last if last > 0 else None,
            "open": open_ if open_ > 0 else None,
            "high": high if high > 0 else None,
            "low": low if low > 0 else None,
            "close": close if close > 0 else None,
            "intraday_pct": intraday,
            "vs_1d_pct": vs_1d,
            "vs_1d_usd_pct": vs_1d_usd,
            "bid": bid if bid > 0 else None,
            "offer": offer if offer > 0 else None,
            "spread": spread if spread > 0 else None,
            "spread_pct": spread_pct,
            "vwap": vwap if vwap > 0 else None,
            "volume": volume if volume > 0 else None,
            "total_money": total_money if total_money > 0 else None,
            "adr_last": adr.get("adr_last"),
            "adr_fecha": adr.get("adr_fecha"),
            "adr_intraday": adr.get("adr_intraday"),
            "adr_vs_1d_pct": adr.get("adr_vs_1d_pct"),
            "adr_ret_wtd_pct": adr.get("adr_ret_wtd_pct"),
            "adr_ret_7d_pct": adr.get("adr_ret_7d_pct"),
            "adr_ret_15r_pct": adr.get("adr_ret_15r_pct"),
            "adr_ret_mtd_pct": adr.get("adr_ret_mtd_pct"),
            "adr_ret_ytd_pct": adr.get("adr_ret_ytd_pct"),
            "adr_dollar_vol": adr.get("adr_dollar_vol"),
            "updated_at": s.get("updated_at"),
        })
    return out


@cached(ttl=300)
def get_universo() -> list[dict]:
    """Catálogo de CEDEARs activos (master categórico, sin precios). Una fila por
    CEDEAR con su clasificación de negocio. Útil para descubrir qué papeles existen
    y filtrar por sector/rubro/región/IA antes de pedir live o quant. Lee
    `mercado.cedears WHERE activo IS TRUE`."""
    out: list[dict] = []
    for m in _master_activos():
        out.append({
            "ticker_corto": m.get("ticker_corto"),
            "nombre": m.get("nombre"),
            "underlying": m.get("underlying"),
            "ratio_cedear": m.get("ratio_cedear"),
            "sector": m.get("sector"),
            "rubro": m.get("rubro"),
            "es_ia": m.get("es_ia"),
            "industria": m.get("industria"),
            "region": m.get("region"),
            "pais": m.get("pais"),
        })
    out.sort(key=lambda d: (d.get("ticker_corto") or ""))
    return out


@cached(ttl=60)
def get_ticker_returns(ticker: str) -> dict:
    """Retornos diarios aritméticos del underlying desde
    `mercado.precios_acciones`.

    Devuelve los retornos sueltos (los consume el histograma) Y la SERIE con su
    fecha (`serie`: [{fecha, ret_pct}]), que es lo que permite graficarlos en el
    tiempo. Son los mismos números: `ret_pct` es el retorno × 100.

    Las fechas se emparejan con los cierres ANTES de calcular: si un día viene
    con `close` nulo se descarta el par completo. Antes los nulos se filtraban
    solo del lado de los precios, así que un hueco corría todas las fechas
    posteriores y cada retorno quedaba pegado al día equivocado."""
    from quant.rolling_stats import returns_from_prices

    underlying = _resolve_underlying(ticker)
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT fecha, close FROM mercado.precios_acciones "
            "WHERE ticker = %s ORDER BY fecha",
            (underlying,),
        )
        docs = cur.fetchall()
    vacio = {"ticker": ticker.upper(), "returns": [], "serie": [],
             "last_return": None, "last_fecha": None}
    if not docs:
        return vacio
    pares = [(d["fecha"], float(d["close"])) for d in docs if d.get("close") is not None]
    if not pares:
        return vacio
    fechas = [f for f, _c in pares]
    rets = returns_from_prices([c for _f, c in pares])
    # el primer día no tiene retorno (no hay contra qué compararlo)
    serie = [{"fecha": f.isoformat() if hasattr(f, "isoformat") else str(f),
              "ret_pct": round(r * 100, 4)}
             for f, r in zip(fechas[1:], rets, strict=True)]
    return {
        "ticker": ticker.upper(),
        "returns": rets,
        "serie": serie,
        "last_return": rets[-1] if rets else None,
        "last_fecha": serie[-1]["fecha"] if serie else None,
    }


@cached(ttl=60)
def get_pivot_points(ticker: str) -> dict:
    """Pivot points en 4 timeframes del subyacente USD. Mismo cálculo
    (quant.pivot_points.obtener_4_timeframes — lee mercado.precios_acciones SQL desde
    el cutover 2026-06-24), con el `last` pisado por el live del ADR desde
    mercado.adr_snapshot.
    """
    from quant.pivot_points import obtener_4_timeframes

    underlying = _resolve_underlying(ticker)
    res = obtener_4_timeframes(underlying)

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT data FROM mercado.adr_snapshot WHERE ticker = %s LIMIT 1",
            (underlying,),
        )
        row = cur.fetchone()
    snap = row["data"] if row else None
    if snap and snap.get("c"):
        res["last"] = float(snap["c"])
        res["last_source"] = "live"
        ua = snap.get("updated_at")
        if isinstance(ua, str):
            res["last_fecha"] = ua
        elif isinstance(ua, datetime):
            res["last_fecha"] = ua.isoformat()
    else:
        res["last_source"] = "eod"
    return res


@cached(ttl=60)
def get_quant_stats(ticker: str, window: int = 60) -> dict:
    """Stats rolling (beta/alpha/corr vs SPY/QQQ + vol + zscore) sobre
    mercado.precios_acciones. Shape idéntico a scanner.get_quant_stats."""
    from quant.rolling_stats import (
        beta_alpha,
        correlation,
        realized_vol,
        returns_from_prices,
        zscore_last,
    )

    underlying = _resolve_underlying(ticker)
    closes_a = _serie_closes(underlying)
    closes_spy = _serie_closes("SPY")
    closes_qqq = _serie_closes("QQQ")

    if not closes_a:
        return {
            "ticker": ticker.upper(),
            "last": None,
            "n_observations": 0,
            "beta": {"spy": None, "qqq": None},
            "alpha": {"spy": None, "qqq": None},
            "corr": {"spy": None, "qqq": None},
            "vol": {"d30": None, "d60": None},
            "zscore": {"d30": None, "d60": None},
        }

    rets_a = returns_from_prices(closes_a)
    rets_spy = returns_from_prices(closes_spy)
    rets_qqq = returns_from_prices(closes_qqq)

    n = min(len(rets_a), len(rets_spy), len(rets_qqq), window)
    a_w = rets_a[-n:]
    spy_w = rets_spy[-n:]
    qqq_w = rets_qqq[-n:]

    ba_spy = beta_alpha(a_w, spy_w)
    ba_qqq = beta_alpha(a_w, qqq_w)
    corr_spy = correlation(a_w, spy_w)
    corr_qqq = correlation(a_w, qqq_w)

    vol_30 = realized_vol(rets_a[-30:]) if len(rets_a) >= 30 else None
    vol_60 = realized_vol(rets_a[-60:]) if len(rets_a) >= 60 else None
    z_30 = zscore_last(rets_a[-30:]) if len(rets_a) >= 30 else None
    z_60 = zscore_last(rets_a[-60:]) if len(rets_a) >= 60 else None

    return {
        "ticker": ticker.upper(),
        "last": closes_a[-1] if closes_a else None,
        "n_observations": n,
        "beta": {"spy": ba_spy["beta"], "qqq": ba_qqq["beta"]},
        "alpha": {"spy": ba_spy["alpha"], "qqq": ba_qqq["alpha"]},
        "corr": {"spy": corr_spy, "qqq": corr_qqq},
        "vol": {"d30": vol_30, "d60": vol_60},
        "zscore": {"d30": z_30, "d60": z_60},
    }


# ── tape intradía: SQL-native (mercado.cedears_time_sales) ───────────────────
# El tape de CEDEARs migró de Mongo a SQL en el decomiso 2026-06-28: motor_cedears
# escribe mercado.cedears_time_sales (intradía, se vacía al cierre) y los readers
# de scanner.py ya leen SQL. Estos wrappers delegan en scanner.py → SQL-native de
# punta a punta. Se mantiene la delegación para no duplicar las dos queries.
def get_cedears_trades(*, ticker: str, limite: int = 200) -> list[dict]:
    from api.services.scanner import get_cedears_trades as _m
    return _m(ticker=ticker, limite=limite)


def get_cedears_intraday(*, ticker: str) -> list[dict]:
    from api.services.scanner import get_cedears_intraday as _m
    return _m(ticker=ticker)
