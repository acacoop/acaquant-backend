"""cedears_bars_1m.py — archiva el tape intradía de CEDEARs como barras de 1 min.

mercado.cedears_time_sales (el tape) es INTRADÍA y se VACÍA al cierre (cron
jobs/cleanup_cedears_timesales.py, 23:50 UTC) → si no lo guardamos antes, la
microestructura del día se pierde para siempre. Este job resamplea el tape del
día a OHLCV por minuto y lo persiste en mercado.cedears_bars_1m (archivo
PERMANENTE), para poder derivar el Efficiency Ratio intradía (y cualquier
métrica de 1 min) en vivo E histórico — la fuente de verdad son las barras, no
un snapshot cacheado.

Corre CON EL MOTOR YA PARADO (cron 20:20 UTC L-V; motor_cedears para 20:05) y
ANTES del cleanup (23:50) → el tape tiene el día completo y estable.

VENTANA MÓVIL: mantiene solo las últimas RUEDAS_KEEP ruedas (poda por fecha del
minuto) → la tabla no crece sin control. IDEMPOTENTE: upsert por (ticker, minuto).

Uso:
    python -m jobs.cedears_bars_1m
    python -m jobs.cedears_bars_1m --dry     # no persiste, solo cuenta
"""
from __future__ import annotations

import argparse
import logging
import sys

from psycopg.rows import dict_row

from core import pg_mirror
from core.postgres import get_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("CedearsBars1m")

RUEDAS_KEEP = 60  # ventana móvil: cuántas ruedas de barras 1-min guardar

DDL = """
CREATE TABLE IF NOT EXISTS mercado.cedears_bars_1m (
    ticker_corto text NOT NULL,
    minuto       timestamptz NOT NULL,
    open         numeric,
    high         numeric,
    low          numeric,
    close        numeric,
    volume       numeric,
    trades       integer,
    PRIMARY KEY (ticker_corto, minuto)
);
CREATE INDEX IF NOT EXISTS ix_cedears_bars_1m_tk_min
    ON mercado.cedears_bars_1m (ticker_corto, minuto DESC);
"""


def _resamplear() -> list[dict]:
    """Agrega el tape vivo a OHLCV por (ticker_corto, minuto). open/close por
    orden temporal del tick; high/low por precio; volume = Σ size; trades = #."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT ticker_corto,
                   date_trunc('minute', ts) AS minuto,
                   (array_agg(price ORDER BY ts ASC,  id ASC ))[1] AS open,
                   max(price)                                       AS high,
                   min(price)                                       AS low,
                   (array_agg(price ORDER BY ts DESC, id DESC))[1] AS close,
                   sum(size)                                        AS volume,
                   count(*)                                         AS trades
            FROM mercado.cedears_time_sales
            WHERE price > 0 AND ticker_corto IS NOT NULL
            GROUP BY ticker_corto, date_trunc('minute', ts)
            """
        )
        return cur.fetchall()


def _podar(keep: int) -> int:
    """Borra las barras de las ruedas fuera de las últimas `keep` (por fecha)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM mercado.cedears_bars_1m
            WHERE minuto::date < (
                SELECT MIN(d) FROM (
                    SELECT DISTINCT minuto::date AS d FROM mercado.cedears_bars_1m
                    ORDER BY d DESC LIMIT %s
                ) t
            )
            """,
            (keep,),
        )
        n = cur.rowcount or 0
        conn.commit()
    return n


def _run(dry: bool) -> int:
    if not dry:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(DDL)
            conn.commit()

    rows = _resamplear()
    if not rows:
        logger.warning("tape vacío — ¿no hubo rueda o motor caído?")
        return 0

    if dry:
        logger.info("[DRY] archivaría %d barras de 1 min", len(rows))
        return len(rows)

    pg_mirror.write_native(
        "mercado.cedears_bars_1m", ["ticker_corto", "minuto"], [dict(r) for r in rows]
    )
    podadas = _podar(RUEDAS_KEEP)
    if podadas:
        logger.info("podadas %d barras (ventana %d ruedas)", podadas, RUEDAS_KEEP)
    logger.info("archivadas %d barras de 1 min", len(rows))
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="No persiste, solo cuenta")
    args = parser.parse_args()
    if args.dry:
        _run(dry=True)
        return 0
    from core.job_runs import JobRunLogger
    with JobRunLogger("cedears_bars_1m") as jr:
        n = _run(dry=False)
        jr.set_stat("barras", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
