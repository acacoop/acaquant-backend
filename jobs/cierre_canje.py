"""cierre_canje.py — materializa el cierre diario de los tickers de canje.

Persiste en Trading.CanjeCierre el último precio del día de cada ticker C/D de
config.PARES_CANJE (AL30C/D, GD30C/D). Así api/services/canje.py::serie_canje
lee ~365 docs por par en vez de agregar ~540k ticks de TimeSales (era 2.6s cold).

Corre post-cierre (el motor para 17:05 ART; este cron va 17:30 ART = 20:30 UTC).
Lee el último trade del día desde Trading.TimeSales (1 doc por ticker vía índice
(ticker, timestamp)). GUARD: precio <= 0 → skip (no persiste cierres stale).

IDEMPOTENTE: upsert por (ticker, fecha). Re-correr el mismo día pisa con el
mismo valor.

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
from core.job_runs import JobRunLogger
from core.pg_mirror import write_native

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("CierreCanje")


def _tickers() -> list[str]:
    return [t for par in PARES_CANJE.values() for t in (par["c"], par["d"])]


def _ultimo_trade_dia(db, ticker: str, dia: date) -> float | None:
    # Último trade del día desde SQL mercado.timesales (Trading.TimeSales DROPEADA
    # 2026-06-22). ts naive ART → bounds naive del día ART. `db` ya no se usa acá.
    from core.postgres import get_pool
    inicio = datetime.combine(dia, datetime.min.time())
    fin = inicio + timedelta(days=1)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT price FROM mercado.timesales WHERE ticker = %s AND price > 0 "
            "AND ts >= %s AND ts < %s ORDER BY ts DESC LIMIT 1",
            (ticker, inicio, fin),
        )
        row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else None


def run(fecha: date, dry: bool = False) -> int:
    # SQL-NATIVE: Trading.CanjeCierre (Mongo) fue migrada → dropeada. Escribe directo a
    # mercado.canje_cierre (upsert por ticker+fecha). El último trade sale de mercado.timesales.
    f_iso = fecha.isoformat()
    escritos = 0
    pg_rows = []
    for tk in _tickers():
        price = _ultimo_trade_dia(None, tk, fecha)
        if not price or price <= 0:
            logger.warning("sin trade %s en %s → skip", tk, f_iso)
            continue
        if dry:
            logger.info("[dry] %s %s = %.4f", f_iso, tk, price)
            escritos += 1
            continue
        pg_rows.append({"ticker": tk, "fecha": fecha, "price": price,
                        "updated_at": datetime.now(UTC)})
        escritos += 1
        logger.info("%s %s = %.4f", f_iso, tk, price)

    write_native("mercado.canje_cierre", ["ticker", "fecha"], pg_rows)
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
    if args.dry:
        run(fecha, dry=True)
        return

    esperados = len(_tickers())
    with JobRunLogger("cierre_canje") as jr:
        escritos = run(fecha, dry=False)
        jr.set_stat("fecha", fecha.isoformat())
        jr.set_stat("escritos", escritos)
        jr.set_stat("esperados", esperados)
        if escritos < esperados:
            jr.error(f"{esperados - escritos} tickers sin trade en {fecha.isoformat()}")


if __name__ == "__main__":
    main()
