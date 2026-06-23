"""day_trading_stats.py — resumen diario de scalping por CEDEAR.

El tape intradía (Trading.CedearsTimeSales) SE VACÍA al cierre
(jobs.cleanup_cedears_timesales, 20:10 UTC) — este job corre ANTES
(cron 20:06 UTC, motor parado 20:05) y persiste lo que el TRADE LAB
necesita recordar de cada rueda:

    Trading.DayTradingStats (1 doc por fecha+ticker):
      vueltas_05 / vueltas_075 / vueltas_10 / vueltas_15  (patas zigzag por
        umbral — mismos buckets que los presets de la UI),
      rango_pct, total_money, flujo_compra_pct, n_minutos.

Con ~20 ruedas acumuladas, el lab muestra la "costumbre" del papel
(vueltas PROMEDIO por rueda) además de las del día — rankear por hábito y
no solo por la foto de hoy.

Reusa helpers de api/services/day_trading (misma excepción de capa que
snapshot_sinteticos / pnl_totales_precompute: un job de precompute puede
reusar un service de api/).

IDEMPOTENTE: upsert por (fecha, ticker); re-correrlo el mismo día pisa con
lo mismo. Liviano: UNA aggregation sobre el tape del día (solo hoy en la
colección) — sin scans históricos (REGLA #4).

Uso:
    python -m jobs.day_trading_stats          # rueda de hoy (UTC)
    python -m jobs.day_trading_stats --dry    # no persiste, imprime resumen
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime

from api.services.day_trading import UMBRALES_STATS, campo_vueltas
from core import pg_mirror
from core.mongo import get_mongo_client
from quant.intraday import analizar_vueltas

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("DayTradingStats")


def _minutos_por_ticker(db) -> dict[str, list[dict]]:
    """{ticker_corto: [{m, c, bm, sm} asc]} del tape de HOY (1 aggregation)."""
    inicio_hoy = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    cur = db["CedearsTimeSales"].aggregate([
        {"$match": {"timestamp": {"$gte": inicio_hoy}}},
        {"$sort": {"timestamp": 1}},
        {"$group": {
            "_id": {
                "tk": "$ticker_corto",
                "m": {"$dateToString": {"format": "%Y-%m-%dT%H:%M:00Z", "date": "$timestamp"}},
            },
            "c": {"$last": "$price"},
            "bm": {"$sum": {"$cond": [{"$eq": ["$side", "BUY"]}, "$money", 0]}},
            "sm": {"$sum": {"$cond": [{"$eq": ["$side", "SELL"]}, "$money", 0]}},
            "tm": {"$sum": "$money"},
        }},
        {"$sort": {"_id.m": 1}},
    ])
    out: dict[str, list[dict]] = {}
    for d in cur:
        tk = (d["_id"] or {}).get("tk")
        if not tk or d.get("c") is None:
            continue
        out.setdefault(tk, []).append({
            "c": float(d["c"]),
            "bm": float(d.get("bm") or 0),
            "sm": float(d.get("sm") or 0),
            "tm": float(d.get("tm") or 0),
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="No persiste, solo imprime resumen")
    args = parser.parse_args()

    if args.dry:
        _run(dry=True)
        return 0
    from core.job_runs import JobRunLogger
    with JobRunLogger("day_trading_stats") as jr:
        n = _run(dry=False)
        jr.set_stat("tickers", n)
    return 0


def _run(dry: bool) -> int:
    client = get_mongo_client()
    db = client["Trading"]
    fecha = datetime.now(UTC).date().isoformat()

    col = db["DayTradingStats"]
    col.create_index([("fecha", 1), ("ticker", 1)], unique=True, name="uq_fecha_ticker")
    col.create_index([("ticker", 1), ("fecha", -1)], name="ix_ticker_fecha")

    minutos = _minutos_por_ticker(db)
    if not minutos:
        logger.warning("[%s] tape vacío — ¿corrió después del cleanup o no hubo rueda?", fecha)
        return 0

    n = 0
    sql_rows: list[dict] = []
    for tk, mins in minutos.items():
        closes = [x["c"] for x in mins]
        if len(closes) < 2:
            continue
        lo, hi = min(closes), max(closes)
        bm = sum(x["bm"] for x in mins)
        sm = sum(x["sm"] for x in mins)
        doc = {
            "fecha":      fecha,
            "ticker":     tk,
            "rango_pct":  round((hi - lo) / lo * 100, 2) if lo > 0 and hi > lo else 0.0,
            "total_money": round(sum(x["tm"] for x in mins), 0),
            "flujo_compra_pct": round(bm / (bm + sm) * 100, 1) if (bm + sm) > 0 else None,
            "n_minutos":  len(mins),
            "updated_at": datetime.now(UTC),
        }
        for u in UMBRALES_STATS:
            doc[campo_vueltas(u)] = analizar_vueltas(closes, u)["vueltas"]
        if not dry:
            col.update_one({"fecha": fecha, "ticker": tk}, {"$set": doc}, upsert=True)
            # Espejo SQL (flag MERCADO_SQL_WRITE, best-effort): passthrough jsonb a
            # mercado.day_trading_stats. fecha (str ISO) la coacciona psycopg a date.
            sql_rows.append({"fecha": fecha, "ticker": tk, "data": pg_mirror.doc_iso(doc)})
        n += 1

    if not dry:
        pg_mirror.mirror_job("mercado.day_trading_stats", ["fecha", "ticker"], sql_rows)

    logger.info("[%s] persistidos %d tickers en Trading.DayTradingStats%s",
                fecha, n, " (DRY)" if dry else "")
    return n


if __name__ == "__main__":
    sys.exit(main())
