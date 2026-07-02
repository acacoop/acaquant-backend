"""bonos_ohlc_daily.py — guarda el OHLC diario de cada bono (ventana móvil).

Gemelo de jobs/cedears_ohlc_daily.py, pero para renta fija. El mercado nos da
OP/HI/LO/LA por ticker y motor_rofex (engines/valores.py) los deja en
mercado.market_snapshot, pero esa foto se pisa cada día. Este job corre tras el
cierre (cron 20:15 UTC L-V; el motor para 20:05 → el snapshot queda congelado con
los valores finales del día) y copia el OHLC de cada bono a
mercado.bonos_ohlc_daily, para poder calcular pivots sobre el bono.

  close del día = `last_price` (último precio). El `closing_price` del snapshot es
  el cierre de AYER (feed) → NO se usa. Igual criterio que cedears_ohlc_daily.

Universo: mercado.curvas (todo el master de renta fija — soberanos, CER, tasa
fija, ONs). El que no operó en el día queda afuera por el guard de OHLC válido.

VENTANA MÓVIL: mantiene solo las últimas RUEDAS_KEEP ruedas (borra las más
viejas) → la tabla nunca crece. IDEMPOTENTE: upsert por (ticker_corto, fecha);
re-correr el mismo día pisa con lo mismo. Se construye HACIA ADELANTE (no
backfilleable: el snapshot es solo la foto de hoy).

Uso:
    python -m jobs.bonos_ohlc_daily          # rueda de hoy (UTC)
    python -m jobs.bonos_ohlc_daily --dry     # no persiste, solo imprime
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from core import curvas_sql, pg_mirror
from core.market_snapshot import snapshot_docs
from core.postgres import get_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("BonosOHLCDaily")

RUEDAS_KEEP = 20  # ventana móvil: cuántas ruedas guardar

DDL = """
CREATE TABLE IF NOT EXISTS mercado.bonos_ohlc_daily (
    ticker_corto text NOT NULL,
    fecha        date NOT NULL,
    open         numeric,
    high         numeric,
    low          numeric,
    close        numeric,
    PRIMARY KEY (ticker_corto, fecha)
);
CREATE INDEX IF NOT EXISTS ix_bonos_ohlc_tk_fecha
    ON mercado.bonos_ohlc_daily (ticker_corto, fecha DESC);
"""


def _f(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _fila(ticker_corto: str, metrics: dict, fecha) -> dict | None:
    """Arma la fila OHLC del día desde metrics del market_snapshot. None si el bono
    no operó (sin high/low/last válidos)."""
    high, low = _f(metrics.get("high_price")), _f(metrics.get("low_price"))
    last, open_ = _f(metrics.get("last_price")), _f(metrics.get("open_price"))
    if not ticker_corto or not high or not low or not last or high <= 0 or low <= 0 or last <= 0:
        return None
    return {
        "ticker_corto": ticker_corto,
        "fecha": fecha,
        "open": open_ if (open_ and open_ > 0) else None,
        "high": high,
        "low": low,
        "close": last,  # close del día = último precio
    }


def _bonos_universo() -> dict[str, str]:
    """{ticker_largo: ticker_corto} de todo el master de renta fija (mercado.curvas)."""
    out: dict[str, str] = {}
    for d in curvas_sql.cargar_todos():
        tk, tc = d.get("ticker"), d.get("ticker_corto")
        if tk and tc:
            out[tk] = tc
    return out


def _podar(keep: int) -> int:
    """Borra las ruedas fuera de las últimas `keep` (por fecha distinta)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM mercado.bonos_ohlc_daily
            WHERE fecha < (
                SELECT MIN(fecha) FROM (
                    SELECT DISTINCT fecha FROM mercado.bonos_ohlc_daily
                    ORDER BY fecha DESC LIMIT %s
                ) t
            )
            """,
            (keep,),
        )
        n = cur.rowcount or 0
        conn.commit()
    return n


def _run(dry: bool) -> int:
    hoy = datetime.now(UTC).date()
    if not dry:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(DDL)
            conn.commit()

    universo = _bonos_universo()
    if not universo:
        logger.warning("Sin bonos en mercado.curvas — ¿master vacío?")
        return 0

    snaps = snapshot_docs(list(universo.keys()))
    rows: list[dict] = []
    for ticker_largo, ticker_corto in universo.items():
        snap = snaps.get(ticker_largo)
        if not snap:
            continue
        fila = _fila(ticker_corto, snap.get("metrics") or {}, hoy)
        if fila:
            rows.append(fila)

    if not rows:
        logger.warning("[%s] snapshot sin OHLC válido — ¿no hubo rueda o motor caído?", hoy)
        return 0

    if not dry:
        pg_mirror.write_native("mercado.bonos_ohlc_daily", ["ticker_corto", "fecha"], rows)
        podadas = _podar(RUEDAS_KEEP)
        if podadas:
            logger.info("podadas %d filas (ventana %d ruedas)", podadas, RUEDAS_KEEP)

    logger.info("[%s] guardado OHLC de %d bonos%s", hoy, len(rows), " (DRY)" if dry else "")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="No persiste, solo imprime")
    args = parser.parse_args()
    if args.dry:
        _run(dry=True)
        return 0
    from core.job_runs import JobRunLogger
    with JobRunLogger("bonos_ohlc_daily") as jr:
        n = _run(dry=False)
        jr.set_stat("bonos", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
