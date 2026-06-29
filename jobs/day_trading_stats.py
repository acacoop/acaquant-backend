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

from psycopg.rows import dict_row

from api.services.day_trading import UMBRALES_STATS, campo_vueltas
from core import pg_mirror
from core.postgres import get_pool
from quant.intraday import analizar_vueltas

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("DayTradingStats")


def _minutos_por_ticker() -> dict[str, list[dict]]:
    """{ticker_corto: [{c, bm, sm, tm} asc]} del tape de HOY (1 query SQL).

    Lee mercado.cedears_time_sales (SQL-native desde el decomiso 2026-06-28).
    c = último precio del minuto · bm/sm = plata comprada/vendida · tm = plata
    total del minuto."""
    inicio_hoy = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
                ticker_corto,
                (array_agg(price ORDER BY ts DESC, id DESC))[1] AS c,
                COALESCE(sum(money) FILTER (WHERE side = 'BUY'), 0)  AS bm,
                COALESCE(sum(money) FILTER (WHERE side = 'SELL'), 0) AS sm,
                COALESCE(sum(money), 0)                             AS tm
            FROM mercado.cedears_time_sales
            WHERE ts >= %s
            GROUP BY ticker_corto, date_trunc('minute', ts AT TIME ZONE 'UTC')
            ORDER BY date_trunc('minute', ts AT TIME ZONE 'UTC')
            """,
            (inicio_hoy,),
        )
        rows = cur.fetchall()
    out: dict[str, list[dict]] = {}
    for d in rows:
        tk = d["ticker_corto"]
        if not tk or d["c"] is None:
            continue
        out.setdefault(tk, []).append({
            "c": float(d["c"]),
            "bm": float(d["bm"] or 0),
            "sm": float(d["sm"] or 0),
            "tm": float(d["tm"] or 0),
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
    # SQL-native (decomiso 2026-06-29): escribe SOLO mercado.day_trading_stats
    # (write_native, incondicional). El tape ya se lee de SQL. Cero Mongo.
    fecha = datetime.now(UTC).date().isoformat()

    minutos = _minutos_por_ticker()
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
        # passthrough jsonb a mercado.day_trading_stats. fecha (str ISO) la coacciona psycopg a date.
        sql_rows.append({"fecha": fecha, "ticker": tk, "data": pg_mirror.doc_iso(doc)})
        n += 1

    if not dry:
        pg_mirror.write_native("mercado.day_trading_stats", ["fecha", "ticker"], sql_rows)

    logger.info("[%s] persistidos %d tickers en mercado.day_trading_stats%s",
                fecha, n, " (DRY)" if dry else "")
    return n


if __name__ == "__main__":
    sys.exit(main())
