"""cierre_canje.py — materializa el cierre diario de los tickers de canje.

Persiste en Trading.CanjeCierre el último precio del día de cada ticker C/D de
config.PARES_CANJE (AL30C/D, GD30C/D). Así api/services/canje.py::serie_canje
lee ~365 docs por par en vez de agregar ~540k ticks de TimeSales (era 2.6s cold).

Corre post-cierre (el motor para 17:05 ART; este cron va 17:30 ART = 20:30 UTC).
Lee el último trade del día desde Trading.TimeSales (1 doc por ticker vía índice
(ticker, timestamp)). GUARD: precio <= 0 → skip (no persiste cierres stale).

IDEMPOTENTE: upsert por (ticker, fecha). Re-correr el mismo día pisa con el
mismo valor. Backfill histórico inicial: scripts/backfill_cierre_canje.py.

Uso:
    python -m jobs.cierre_canje              # cierre del día UTC actual
    python -m jobs.cierre_canje --fecha 2026-05-20
    python -m jobs.cierre_canje --dry        # no persiste, imprime resumen
"""
from __future__ import annotations

import argparse
import logging
from datetime import UTC, date, datetime, timedelta

from config import PARES_CANJE
from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("CierreCanje")


def _tickers() -> list[str]:
    return [t for par in PARES_CANJE.values() for t in (par["c"], par["d"])]


def _ultimo_trade_dia(db, ticker: str, dia: date) -> float | None:
    inicio = datetime.combine(dia, datetime.min.time(), tzinfo=UTC)
    fin = inicio + timedelta(days=1)
    doc = db["TimeSales"].find_one(
        {"ticker": ticker, "price": {"$gt": 0},
         "timestamp": {"$gte": inicio, "$lt": fin}},
        sort=[("timestamp", -1)],
        projection={"_id": 0, "price": 1},
    )
    return float(doc["price"]) if doc else None


def run(fecha: date, dry: bool = False) -> int:
    client = get_mongo_client()
    db = client["Trading"]
    col = db["CanjeCierre"]
    col.create_index([("ticker", 1), ("fecha", 1)], unique=True)

    f_iso = fecha.isoformat()
    escritos = 0
    for tk in _tickers():
        price = _ultimo_trade_dia(db, tk, fecha)
        if not price or price <= 0:
            logger.warning("sin trade %s en %s → skip", tk, f_iso)
            continue
        if dry:
            logger.info("[dry] %s %s = %.4f", f_iso, tk, price)
            escritos += 1
            continue
        col.update_one(
            {"ticker": tk, "fecha": f_iso},
            {"$set": {"price": price, "updated_at": datetime.now(UTC)}},
            upsert=True,
        )
        escritos += 1
        logger.info("%s %s = %.4f", f_iso, tk, price)

    logger.info("cierre_canje %s: %d/%d tickers", f_iso, escritos, len(_tickers()))
    return escritos


def main() -> None:
    ap = argparse.ArgumentParser(description="Materializa cierre diario de canje")
    ap.add_argument("--fecha", help="YYYY-MM-DD (default: hoy UTC)")
    ap.add_argument("--dry", action="store_true", help="no persiste, solo imprime")
    args = ap.parse_args()

    fecha = (
        datetime.strptime(args.fecha, "%Y-%m-%d").date()
        if args.fecha else datetime.now(UTC).date()
    )
    run(fecha, dry=args.dry)


if __name__ == "__main__":
    main()
