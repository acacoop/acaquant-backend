"""backfill_precios_acciones.py — historia EOD (Yahoo) para los CEDEARs → SQL.

`jobs/precios_acciones_daily` solo trae 5 días (colchón del cron). Cuando se dan de
alta CEDEARs nuevos (`scripts/add_cedears_bulk`), sus underlyings no tienen historia
→ el scanner no puede calcular MTD/YTD/pivots. Este backfill trae ~N días hacia atrás
para cada underlying activo y los UPSERTEA por (ticker, fecha) en
mercado.precios_acciones (SQL-native, sin Mongo). El universo sale de mercado.cedears.

Scopeado al universo de CEDEARs activos, batcheado + throttle (REGLA #4). Dry-run
por default.

    python -m scripts.backfill_precios_acciones --days 400                       # dry-run (cuenta)
    python -m scripts.backfill_precios_acciones --days 400 --apply               # escribe TODOS
    python -m scripts.backfill_precios_acciones --days 400 --solo-nuevos --apply # solo los SIN historia
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import UTC, datetime, timedelta

from core import pg_mirror
from core.postgres import get_pool
from core.yahoo import YahooError, stock_candle

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _underlyings_activos() -> list[str]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT upper(COALESCE(underlying, ticker_corto)) "
            "FROM mercado.cedears WHERE activo IS TRUE")
        return sorted(r[0] for r in cur.fetchall())


def _con_historia() -> set[str]:
    """Underlyings que YA tienen al menos una vela en mercado.precios_acciones."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT upper(ticker) FROM mercado.precios_acciones")
        return {r[0] for r in cur.fetchall()}


def _backfill_ticker(ticker: str, days: int, apply: bool) -> tuple[int, str | None]:
    end_dt = datetime.now(UTC)
    start_dt = end_dt - timedelta(days=days)
    try:
        res = stock_candle(ticker, "D", int(start_dt.timestamp()), int(end_dt.timestamp()))
    except YahooError as e:
        return 0, f"yahoo: {e}"
    if res.get("s") != "ok":
        return 0, f"status={res.get('s')}"

    times = res.get("t") or []
    if not times:
        return 0, "sin velas"
    o, h, lo, c, v = (res.get(k) or [] for k in ("o", "h", "l", "c", "v"))
    rows = [{
        "ticker": ticker,
        "fecha":  datetime.fromtimestamp(t, tz=UTC).date(),
        "open":   o[i]  if i < len(o)  else None,
        "high":   h[i]  if i < len(h)  else None,
        "low":    lo[i] if i < len(lo) else None,
        "close":  c[i]  if i < len(c)  else None,
        "volume": v[i]  if i < len(v)  else None,
    } for i, t in enumerate(times)]
    if apply:
        pg_mirror.write_native("mercado.precios_acciones", ["ticker", "fecha"], rows)
    return len(rows), None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=400, help="días hacia atrás (default 400 ≈ YTD+)")
    ap.add_argument("--solo-nuevos", action="store_true",
                    help="solo underlyings SIN historia (post-alta de CEDEARs nuevos)")
    ap.add_argument("--apply", action="store_true", help="escribir (default: dry-run)")
    args = ap.parse_args()

    tickers = _underlyings_activos()
    if args.solo_nuevos:
        ya = _con_historia()
        antes = len(tickers)
        tickers = [t for t in tickers if t not in ya]
        print(f"--solo-nuevos: {antes} activos → {len(tickers)} sin historia")

    print(f"{'APLICA' if args.apply else 'DRY-RUN'} — {len(tickers)} underlyings, {args.days} días\n")
    total = 0
    for t in tickers:
        n, err = _backfill_ticker(t, args.days, args.apply)
        if err:
            logger.warning("%-6s FAIL: %s", t, err)
        else:
            logger.info("%-6s %s%d velas", t, "+" if args.apply else "~", n)
            total += n
        time.sleep(0.3)  # anti rate-limit yfinance
    print(f"\n{'Upserteadas' if args.apply else 'Procesaría'}: {total} velas"
          f"{'' if args.apply else ' (DRY-RUN — re-correr con --apply)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
