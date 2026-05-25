"""backfill_cierre_canje.py — puebla Trading.CanjeCierre con el histórico.

One-shot inicial para que serie_canje tenga histórico desde el día 1 (el cron
jobs/cierre_canje solo persiste de hoy en adelante). Acá SÍ usamos el aggregate
pesado sobre TimeSales (el que en vivo tardaba 2.6s) — no importa, corre una vez.

Por cada ticker de config.PARES_CANJE, agrega el último trade de cada día del
rango y upsertea en CanjeCierre (idempotente por (ticker, fecha)).

    python -m scripts.backfill_cierre_canje                      # últimos 400 días
    python -m scripts.backfill_cierre_canje --desde 2024-01-01   # rango explícito
    python -m scripts.backfill_cierre_canje --dry
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta

from config import PARES_CANJE
from core.mongo import get_mongo_client


def _tickers() -> list[str]:
    return [t for par in PARES_CANJE.values() for t in (par["c"], par["d"])]


def _ultimos_diarios(db, tickers: list[str], desde: date, hasta: date) -> dict[str, dict[str, float]]:
    """{ticker: {fecha_iso: ultimo_precio}} agregando TimeSales tick-by-tick.

    Pesado a propósito (corre una vez). Mismo cálculo que hacía serie_canje en
    vivo antes de materializar: último trade del día (sort DESC + $first)."""
    inicio = datetime.combine(desde, datetime.min.time(), tzinfo=UTC)
    fin = datetime.combine(hasta + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    pipeline = [
        {"$match": {"ticker": {"$in": tickers}, "price": {"$gt": 0},
                    "timestamp": {"$gte": inicio, "$lt": fin}}},
        {"$addFields": {"fecha": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}}}},
        {"$sort": {"timestamp": -1}},
        {"$group": {"_id": {"ticker": "$ticker", "fecha": "$fecha"}, "price": {"$first": "$price"}}},
    ]
    out: dict[str, dict[str, float]] = {tk: {} for tk in tickers}
    for r in db["TimeSales"].aggregate(pipeline, allowDiskUse=True):
        out.setdefault(r["_id"]["ticker"], {})[r["_id"]["fecha"]] = float(r["price"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill histórico de Trading.CanjeCierre")
    ap.add_argument("--desde", help="YYYY-MM-DD (default: hoy - 400 días)")
    ap.add_argument("--hasta", help="YYYY-MM-DD (default: hoy UTC)")
    ap.add_argument("--dry", action="store_true", help="no persiste, solo cuenta")
    args = ap.parse_args()

    hasta = datetime.strptime(args.hasta, "%Y-%m-%d").date() if args.hasta else datetime.now(UTC).date()
    desde = datetime.strptime(args.desde, "%Y-%m-%d").date() if args.desde else hasta - timedelta(days=400)

    client = get_mongo_client()
    db = client["Trading"]
    col = db["CanjeCierre"]
    col.create_index([("ticker", 1), ("fecha", 1)], unique=True)

    tickers = _tickers()
    print(f"Agregando TimeSales {desde} → {hasta} para {len(tickers)} tickers (puede tardar)…")
    diarios = _ultimos_diarios(db, tickers, desde, hasta)

    total = 0
    for tk in tickers:
        dias = diarios.get(tk, {})
        print(f"  {tk}: {len(dias)} días")
        if args.dry:
            total += len(dias)
            continue
        for f_iso, price in dias.items():
            col.update_one(
                {"ticker": tk, "fecha": f_iso},
                {"$set": {"price": price, "updated_at": datetime.now(UTC)}},
                upsert=True,
            )
            total += 1
    print(f"{'[dry] ' if args.dry else ''}{total} docs en CanjeCierre.")


if __name__ == "__main__":
    main()
