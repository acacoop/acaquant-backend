"""backfill_precios_acciones.py — historia EOD (Yahoo) para los CEDEARs.

`jobs/precios_acciones_daily` solo trae 5 días (colchón del cron). Cuando se dan de
alta CEDEARs nuevos (`scripts/add_cedears_bulk`), sus underlyings no tienen historia
→ el scanner no puede calcular MTD/YTD/pivots. Este backfill trae ~N días hacia atrás
para cada underlying activo e inserta SOLO las fechas faltantes (idempotente, mismo
patrón que el cron). Espeja a `mercado.precios_acciones` (SQL) vía pg_mirror.

Scopeado al universo de CEDEARs activos (`Trading.Cedears`), batcheado + throttle
(REGLA #4). Dry-run por default.

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
from core.mongo import get_mongo_client
from core.yahoo import YahooError, stock_candle

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _underlyings_activos() -> list[str]:
    db = get_mongo_client()["Trading"]
    out = set()
    for d in db["Cedears"].find({"activo": True}, {"_id": 0, "ticker_corto": 1, "underlying": 1}):
        out.add(d.get("underlying") or d["ticker_corto"])
    return sorted(out)


def _con_historia(col) -> set[str]:
    """Underlyings que YA tienen al menos una vela en Trading.PreciosAcciones."""
    return {t for t in col.distinct("ticker")}


def _backfill_ticker(col, ticker: str, days: int, apply: bool) -> tuple[int, str | None]:
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
    fechas = [datetime.fromtimestamp(t, tz=UTC) for t in times]
    ya = {d["fecha"] for d in col.find(
        {"ticker": ticker, "fecha": {"$in": fechas}}, {"_id": 0, "fecha": 1})}

    o, h, lo, c, v = (res.get(k) or [] for k in ("o", "h", "l", "c", "v"))
    nuevos = []
    for i, fecha in enumerate(fechas):
        if fecha in ya:
            continue
        nuevos.append({
            "fecha": fecha, "ticker": ticker,
            "open":  o[i]  if i < len(o)  else None,
            "high":  h[i]  if i < len(h)  else None,
            "low":   lo[i] if i < len(lo) else None,
            "close": c[i]  if i < len(c)  else None,
            "volume": v[i] if i < len(v)  else None,
        })
    if nuevos and apply:
        col.insert_many(nuevos)
        pg_mirror.mirror_job("mercado.precios_acciones", ["ticker", "fecha"], [{
            "ticker": d["ticker"],
            "fecha":  d["fecha"].date() if hasattr(d["fecha"], "date") else d["fecha"],
            "open": d.get("open"), "high": d.get("high"), "low": d.get("low"),
            "close": d.get("close"), "volume": d.get("volume"),
        } for d in nuevos])
    return len(nuevos), None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=400, help="días hacia atrás (default 400 ≈ YTD+)")
    ap.add_argument("--solo-nuevos", action="store_true",
                    help="solo underlyings SIN historia (post-alta de CEDEARs nuevos)")
    ap.add_argument("--apply", action="store_true", help="escribir (default: dry-run)")
    args = ap.parse_args()

    col = get_mongo_client()["Trading"]["PreciosAcciones"]
    tickers = _underlyings_activos()
    if args.solo_nuevos:
        ya = _con_historia(col)
        antes = len(tickers)
        tickers = [t for t in tickers if t not in ya]
        print(f"--solo-nuevos: {antes} activos → {len(tickers)} sin historia")

    print(f"{'APLICA' if args.apply else 'DRY-RUN'} — {len(tickers)} underlyings, {args.days} días\n")
    total = 0
    for t in tickers:
        n, err = _backfill_ticker(col, t, args.days, args.apply)
        if err:
            logger.warning("%-6s FAIL: %s", t, err)
        else:
            logger.info("%-6s %s%d velas", t, "+" if args.apply else "~", n)
            total += n
        time.sleep(0.3)  # anti rate-limit yfinance
    print(f"\n{'Insertadas' if args.apply else 'Faltarían'}: {total} velas"
          f"{'' if args.apply else ' (DRY-RUN — re-correr con --apply)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
