"""cedears_ohlc_daily.py — guarda el OHLC diario de cada CEDEAR (ventana móvil).

El mercado nos da OP/HI/LO/LA por papel y motor_cedears los deja en
mercado.cedears_snapshot, pero esa foto se pisa cada día. Este job corre tras el
cierre (cron 20:15 UTC L-V; el motor para 20:05 → el snapshot queda congelado con
los valores finales del día) y copia el OHLC de cada CEDEAR a
mercado.cedears_ohlc_daily, para poder calcular pivots sobre el CEDEAR (ARS).

  close del día = `last` (último precio). El campo `close` del snapshot es el
  cierre de AYER (closing_price del feed) → NO se usa.

Lee el snapshot, NO el time sales → no le importa el cleanup del tape (20:10).

ADEMÁS del OHLC persiste el ATR-20 (columna `atr`, en ARS): rango típico diario
del papel = promedio de los últimos 20 true ranges. Se calcula UNA vez por rueda
(quant.rango.atr) usando las ruedas ya guardadas + la de hoy. None hasta tener
21 ruedas de historia (post-deploy se llena solo).

VENTANA MÓVIL: mantiene solo las últimas RUEDAS_KEEP ruedas (borra las más
viejas) → la tabla nunca crece. RUEDAS_KEEP ≥ 21 porque el ATR-20 necesita 20
true ranges (= 21 cierres); 60 deja además historia de ATR para mirar atrás.
IDEMPOTENTE: upsert por (ticker, fecha); re-correr el mismo día pisa con lo
mismo. Se construye HACIA ADELANTE (no backfilleable: el snapshot es solo la
foto de hoy).

Uso:
    python -m jobs.cedears_ohlc_daily          # rueda de hoy (UTC)
    python -m jobs.cedears_ohlc_daily --dry     # no persiste, solo imprime
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from psycopg.rows import dict_row

from core import pg_mirror
from core.postgres import get_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("CedearsOHLCDaily")

RUEDAS_KEEP = 60  # ventana móvil: cuántas ruedas guardar (≥21 para el ATR-20)
ATR_PERIODO = 20  # ruedas del ATR

DDL = """
CREATE TABLE IF NOT EXISTS mercado.cedears_ohlc_daily (
    ticker_corto text NOT NULL,
    fecha        date NOT NULL,
    open         numeric,
    high         numeric,
    low          numeric,
    close        numeric,
    PRIMARY KEY (ticker_corto, fecha)
);
ALTER TABLE mercado.cedears_ohlc_daily ADD COLUMN IF NOT EXISTS atr numeric;
CREATE INDEX IF NOT EXISTS ix_cedears_ohlc_tk_fecha
    ON mercado.cedears_ohlc_daily (ticker_corto, fecha DESC);
"""


def _f(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _fila(data: dict, fecha) -> dict | None:
    """Arma la fila OHLC del día desde el doc del snapshot. None si el papel no
    operó (sin high/low/last válidos)."""
    d = data or {}
    tk = d.get("ticker_corto")
    high, low, last, open_ = _f(d.get("high")), _f(d.get("low")), _f(d.get("last")), _f(d.get("open"))
    if not tk or not high or not low or not last or high <= 0 or low <= 0 or last <= 0:
        return None
    return {
        "ticker_corto": tk,
        "fecha": fecha,
        "open": open_ if (open_ and open_ > 0) else None,
        "high": high,
        "low": low,
        "close": last,  # close del día = último precio
    }


def _snapshot_docs() -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT data FROM mercado.cedears_snapshot")
        return [r["data"] for r in cur.fetchall() if r.get("data")]


def _podar(keep: int) -> int:
    """Borra las ruedas fuera de las últimas `keep` (por fecha distinta)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM mercado.cedears_ohlc_daily
            WHERE fecha < (
                SELECT MIN(fecha) FROM (
                    SELECT DISTINCT fecha FROM mercado.cedears_ohlc_daily
                    ORDER BY fecha DESC LIMIT %s
                ) t
            )
            """,
            (keep,),
        )
        n = cur.rowcount or 0
        conn.commit()
    return n


def _historia_previa(tickers: list[str], hoy) -> dict[str, dict]:
    """{ticker_corto: {highs, lows, closes}} de hasta ATR_PERIODO ruedas ANTERIORES
    a hoy, ascendente por fecha — la base para el ATR (se le agrega la de hoy)."""
    if not tickers:
        return {}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT ticker_corto, high, low, close FROM (
                   SELECT ticker_corto, fecha, high, low, close,
                          row_number() OVER (PARTITION BY ticker_corto
                                             ORDER BY fecha DESC) AS rn
                   FROM mercado.cedears_ohlc_daily
                   WHERE ticker_corto = ANY(%s) AND fecha < %s
               ) t WHERE rn <= %s ORDER BY ticker_corto, fecha ASC""",
            (tickers, hoy, ATR_PERIODO),
        )
        out: dict[str, dict] = {}
        for r in cur.fetchall():
            d = out.setdefault(r["ticker_corto"], {"highs": [], "lows": [], "closes": []})
            d["highs"].append(_f(r["high"]))
            d["lows"].append(_f(r["low"]))
            d["closes"].append(_f(r["close"]))
        return out


def _agregar_atr(rows: list[dict], hoy) -> int:
    """Setea rows[i]['atr'] (ATR-20 en ARS) con hasta 20 ruedas previas + la de
    hoy. None si no hay historia suficiente. → cuántos quedaron con ATR."""
    from quant.rango import atr as _atr
    hist = _historia_previa([r["ticker_corto"] for r in rows], hoy)
    con = 0
    for r in rows:
        h = hist.get(r["ticker_corto"], {"highs": [], "lows": [], "closes": []})
        val = _atr(
            h["highs"] + [r["high"]],
            h["lows"] + [r["low"]],
            h["closes"] + [r["close"]],
            periodo=ATR_PERIODO,
        )
        r["atr"] = val
        con += val is not None
    return con


def _run(dry: bool) -> int:
    hoy = datetime.now(UTC).date()
    if not dry:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(DDL)
            conn.commit()

    docs = _snapshot_docs()
    rows = [f for f in (_fila(d, hoy) for d in docs) if f]
    if not rows:
        logger.warning("[%s] snapshot sin OHLC válido — ¿no hubo rueda o motor caído?", hoy)
        return 0

    if not dry:
        con_atr = _agregar_atr(rows, hoy)
        pg_mirror.write_native("mercado.cedears_ohlc_daily", ["ticker_corto", "fecha"], rows)
        podadas = _podar(RUEDAS_KEEP)
        if podadas:
            logger.info("podadas %d filas (ventana %d ruedas)", podadas, RUEDAS_KEEP)
        logger.info("[%s] ATR-20 calculado para %d/%d CEDEARs", hoy, con_atr, len(rows))

    logger.info("[%s] guardado OHLC de %d CEDEARs%s", hoy, len(rows), " (DRY)" if dry else "")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="No persiste, solo imprime")
    args = parser.parse_args()
    if args.dry:
        _run(dry=True)
        return 0
    from core.job_runs import JobRunLogger
    with JobRunLogger("cedears_ohlc_daily") as jr:
        n = _run(dry=False)
        jr.set_stat("cedears", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
