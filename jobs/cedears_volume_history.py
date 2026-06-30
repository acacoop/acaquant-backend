"""cedears_volume_history.py — perfil de volumen intradía por minuto (RVOL).

El tape (mercado.cedears_time_sales) SE VACÍA al cierre (cleanup 20:10 UTC).
Este job corre ANTES (cron 20:08 UTC, motor parado 20:05) y persiste el volumen
por minuto-del-día de cada CEDEAR en `mercado.cedears_volume_history`.

Con ~20 ruedas acumuladas, la vista TRADING calcula:

    RVOL(t) = vol_acumulado_hoy(t) / vol_acumulado_promedio_20d(t)

RVOL > 1.5 = día con volumen anormal → habilita el Tipo B (S2 agresivo). Es la
Fase 2 de [[project_vista_trading]] — el panel ya funcionaba con el fallback
`dia_volatil` por rango; esto lo afina.

IDEMPOTENTE: upsert por (ticker, fecha, minuto). Liviano: UNA aggregation sobre
el tape de HOY (solo hoy en la tabla) — sin scans históricos (REGLA #4). Poda las
ruedas de más de 45 días para acotar la tabla.

OJO (no se puede backfillear): el tape se borra cada día → el perfil se construye
SOLO hacia adelante. RVOL arranca parcial y queda completo tras ~20 ruedas.

Uso:
    python -m jobs.cedears_volume_history          # rueda de hoy (UTC)
    python -m jobs.cedears_volume_history --dry     # no persiste, imprime resumen
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta

from psycopg.rows import dict_row

from core import pg_mirror
from core.postgres import get_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("CedearsVolumeHistory")

_RETENCION_DIAS = 45


def _volumen_por_minuto() -> list[dict]:
    """[{ticker_corto, minuto 'HH:MM' UTC, volume}] del tape de HOY (1 query)."""
    inicio_hoy = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
                ticker_corto,
                to_char(ts AT TIME ZONE 'UTC', 'HH24:MI') AS minuto,
                COALESCE(sum(size), 0)                    AS volume
            FROM mercado.cedears_time_sales
            WHERE ts >= %s AND size IS NOT NULL
            GROUP BY ticker_corto, to_char(ts AT TIME ZONE 'UTC', 'HH24:MI')
            """,
            (inicio_hoy,),
        )
        return cur.fetchall()


def _podar(fecha_corte) -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM mercado.cedears_volume_history WHERE fecha < %s", (fecha_corte,)
        )
        n = cur.rowcount or 0
        conn.commit()
    return n


def _run(dry: bool) -> int:
    hoy = datetime.now(UTC).date()
    filas = _volumen_por_minuto()
    if not filas:
        logger.warning("[%s] tape vacío — ¿corrió tras el cleanup o no hubo rueda?", hoy)
        return 0

    rows: list[dict] = []
    for f in filas:
        tk = f["ticker_corto"]
        if not tk or f["minuto"] is None:
            continue
        rows.append({
            "ticker_corto": tk,
            "fecha": hoy,
            "minuto": f["minuto"],
            "volume": float(f["volume"] or 0),
        })

    if not dry:
        pg_mirror.write_native(
            "mercado.cedears_volume_history", ["ticker_corto", "fecha", "minuto"], rows
        )
        podadas = _podar(hoy - timedelta(days=_RETENCION_DIAS))
        if podadas:
            logger.info("podadas %d filas > %d días", podadas, _RETENCION_DIAS)

    tickers = len({r["ticker_corto"] for r in rows})
    logger.info("[%s] persistidas %d filas (%d tickers)%s",
                hoy, len(rows), tickers, " (DRY)" if dry else "")
    return tickers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="No persiste, solo imprime")
    args = parser.parse_args()
    if args.dry:
        _run(dry=True)
        return 0
    from core.job_runs import JobRunLogger
    with JobRunLogger("cedears_volume_history") as jr:
        n = _run(dry=False)
        jr.set_stat("tickers", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
