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

from datetime import datetime

from psycopg.rows import dict_row

from api.cache import cached
from api.services.scanner import get_ccl_live  # dolar live (Mongo) — reexport para el router
from core import precios_acciones_sql
from core.postgres import get_pool
from quant.pivot_points import debug_4_timeframes, obtener_4_timeframes, ventana_lectura

__all__ = [
    "get_ccl_live",
    "get_cedears_intraday",
    "get_cedears_scanner",
    "get_cedears_trades",
    "get_pivot_points",
    "get_universo",
    "resolve_underlying",
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


def resolve_underlying(ticker_corto: str) -> str:
    """ticker_corto (BYMA) → underlying (US ticker). Público para otros services."""
    return _resolve_underlying(ticker_corto)


# ── scanner principal ────────────────────────────────────────────────────────
@cached(ttl=2)
def get_cedears_scanner() -> list[dict]:
    """Master + snapshot joined por ticker, con métricas operativas del CEDEAR en
    ARS. Desde 2026-09-10 NO trae las métricas del ADR (`adr_*`) ni `rubro` /
    `es_ia`: ningún consumidor las leía (front, `monitor_sql`, `trading_pivots`)
    y con ellas se iba una lectura de `precios_acciones` para todo el universo
    + otra de `adr_snapshot` en cada hit de un poll de 2 s. El USD del subyacente
    vive en `get_pivot_points` (last) y en Research (Reuters)."""
    master = _master_activos()

    snapshots: dict[str, dict] = {}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT data FROM mercado.cedears_snapshot")
        for r in cur.fetchall():
            d = r["data"]
            if d and d.get("ticker"):
                snapshots[d["ticker"]] = d

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

        out.append({
            "ticker_corto": m["ticker_corto"],
            "nombre": m.get("nombre"),
            "underlying": m.get("underlying"),
            "ratio_cedear": m.get("ratio_cedear"),
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


def _velas_pivot(underlying: str) -> tuple[list[dict], dict | None]:
    """(velas de la ventana de pivots, última vela histórica si la ventana está
    vacía). UNA query por ticker en el caso normal; la segunda solo para un
    ticker sin cierres desde el año previo."""
    velas = precios_acciones_sql.velas_eod(underlying, *ventana_lectura())
    ultima = None if velas else precios_acciones_sql.ultima_vela(underlying)
    return velas, ultima


def debug_pivot_points(ticker: str) -> dict:
    """Detalle paso a paso del cálculo de pivots (Manager → Validaciones)."""
    return debug_4_timeframes(ticker, *_velas_pivot(ticker))


@cached(ttl=60)
def get_pivot_points(ticker: str) -> dict:
    """Pivot points en 4 timeframes del subyacente USD. Mismo cálculo
    (quant.pivot_points.obtener_4_timeframes sobre las velas de
    core.precios_acciones_sql), con el `last` pisado por el live del ADR desde
    mercado.adr_snapshot.
    """
    underlying = _resolve_underlying(ticker)
    res = obtener_4_timeframes(underlying, *_velas_pivot(underlying))

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
