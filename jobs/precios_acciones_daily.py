"""precios_acciones_daily.py — agrega 1 vela daily por activo a
mercado.precios_acciones (SQL-native, sin Mongo).

Diseñado para correr 1×/día post-cierre US. Para cada CEDEAR activo pide a Yahoo
los últimos 5 días (D, OHLCV) y los UPSERTEA por (ticker, fecha) en
mercado.precios_acciones (idempotente — re-correr no duplica). El universo sale
de mercado.cedears (espejo SQL del master, activo=true).

5 días de colchón: si el cron falló un día/festivo, recuperamos esos días
en la próxima corrida sin sumar lógica extra.

**Splits / re-ajustes (incidente pivots 2026-07-11).** Yahoo devuelve precios
crudos (auto_adjust=False) y ante un split RE-AJUSTA toda la historia hacia
atrás; como el daily solo upsertea 5 días, la historia vieja quedaba en la
escala pre-split (caso CRWD) y los pivots anuales/quant salían de otra escala.
Defensa en dos capas:
  1. El daily compara las velas que Yahoo trae contra las YA guardadas de esos
     mismos días: si difieren >10%, Yahoo re-ajustó → re-backfill completo de
     ESE ticker en el momento (auto-reparación, determinista).
  2. `--backfill`: re-descarga la historia completa desde DESDE_BACKFILL para
     las series incompletas (scopeado: solo tickers cuya serie no llega a esa
     fecha, salvo --ticker que fuerza). Repara también velas basura (caso HON).

Cutover SQL-native 2026-06-24: antes escribía Trading.PreciosAcciones (Mongo) +
espejo SQL; ahora escribe SOLO SQL (`pg_mirror.write_native`). Lectores (scanner,
quant/pivot_points) leen SQL. Sin sync ni Mongo. Ver docs/SQL.md.

Cron:
    0 22 * * 1-5  python -m jobs.precios_acciones_daily

Uso manual:
    python -m jobs.precios_acciones_daily                  # corrida normal
    python -m jobs.precios_acciones_daily --ticker NVDA    # uno solo
    python -m jobs.precios_acciones_daily --backfill       # historia completa (series incompletas)
    python -m jobs.precios_acciones_daily --backfill --ticker CRWD  # forzar uno
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import UTC, datetime, timedelta

from core import pg_mirror
from core.job_runs import JobRunLogger
from core.postgres import get_pool
from core.yahoo import YahooError, stock_candle

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DIAS_COLCHON = 5  # cuántos días pedir hacia atrás para recuperar gaps

# Backfill: desde acá tiene que haber serie para que la vela ANUAL (año
# calendario previo completo) y las ventanas de quant (hasta 252 ruedas)
# salgan bien. 2026 en adelante lo cubre el propio daily.
DESDE_BACKFILL = datetime(2024, 1, 1, tzinfo=UTC)
UMBRAL_REAJUSTE = 0.10   # diff >10% en una vela ya guardada = Yahoo re-ajustó (split)
SLEEP_BACKFILL_S = 1.0   # throttle entre tickers en modo backfill (REGLA #4)


def _underlyings_activos(filter_ticker: str | None = None) -> list[str]:
    """UNDERLYINGS (símbolo US) de los CEDEARs activos, desde mercado.cedears (SQL)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        if filter_ticker:
            ft = filter_ticker.upper()
            cur.execute(
                "SELECT DISTINCT upper(COALESCE(underlying, ticker_corto)) "
                "FROM mercado.cedears WHERE activo IS TRUE "
                "AND (upper(ticker_corto) = %s OR upper(underlying) = %s)", (ft, ft))
        else:
            cur.execute(
                "SELECT DISTINCT upper(COALESCE(underlying, ticker_corto)) "
                "FROM mercado.cedears WHERE activo IS TRUE")
        return sorted(r[0] for r in cur.fetchall())


def _velas_yahoo(ticker: str, start_dt: datetime, end_dt: datetime) -> tuple[list[dict], str | None]:
    """Velas D de Yahoo en [start, end] como filas listas para upsertear."""
    try:
        res = stock_candle(ticker, "D", int(start_dt.timestamp()), int(end_dt.timestamp()))
    except YahooError as e:
        return [], f"yahoo: {e}"
    if res.get("s") != "ok":
        return [], f"status={res.get('s')}"

    times = res.get("t") or []
    if not times:
        return [], "sin velas"
    o, h, lo, c, v = (res.get(k) or [] for k in ("o", "h", "l", "c", "v"))
    return [{
        "ticker": ticker,
        "fecha":  datetime.fromtimestamp(t, tz=UTC).date(),
        "open":   o[i]  if i < len(o)  else None,
        "high":   h[i]  if i < len(h)  else None,
        "low":    lo[i] if i < len(lo) else None,
        "close":  c[i]  if i < len(c)  else None,
        "volume": v[i]  if i < len(v)  else None,
    } for i, t in enumerate(times)], None


def _detecta_reajuste(ticker: str, rows: list[dict]) -> bool:
    """True si alguna vela que Yahoo trae difiere >UMBRAL de la MISMA vela ya
    guardada → Yahoo re-ajustó la historia (split) y nuestra serie vieja quedó
    en otra escala. Dispara el re-backfill del ticker."""
    fechas = [r["fecha"] for r in rows]
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT fecha, close FROM mercado.precios_acciones "
            "WHERE ticker = %s AND fecha = ANY(%s)",
            (ticker, fechas),
        )
        guardadas = {r[0]: float(r[1]) for r in cur.fetchall() if r[1] is not None}
    for r in rows:
        prev = guardadas.get(r["fecha"])
        nuevo = r.get("close")
        if prev and nuevo and abs(nuevo / prev - 1) > UMBRAL_REAJUSTE:
            return True
    return False


def backfill_ticker(ticker: str) -> tuple[int, str | None]:
    """Re-descarga la historia COMPLETA del ticker desde DESDE_BACKFILL y la
    upsertea. Como Yahoo sirve la serie re-ajustada de HOY, esto repara splits
    (escala vieja) y velas basura de una pasada. Idempotente."""
    rows, err = _velas_yahoo(ticker, DESDE_BACKFILL, datetime.now(UTC))
    if err:
        return 0, err
    pg_mirror.write_native("mercado.precios_acciones", ["ticker", "fecha"], rows)
    return len(rows), None


def upsert_ticker(ticker: str) -> tuple[int, str | None]:
    """Pide las velas a Yahoo y las upsertea en SQL. Si detecta que Yahoo
    re-ajustó la historia (split), re-backfillea el ticker completo.
    Returns (n_filas, error_msg)."""
    end_dt = datetime.now(UTC)
    start_dt = end_dt - timedelta(days=DIAS_COLCHON)

    rows, err = _velas_yahoo(ticker, start_dt, end_dt)
    if err:
        return 0, err
    if _detecta_reajuste(ticker, rows):
        logger.warning("%s: Yahoo re-ajustó la historia (¿split?) → re-backfill completo", ticker)
        return backfill_ticker(ticker)
    # SQL-native: upsert incondicional por (ticker, fecha). Idempotente.
    pg_mirror.write_native("mercado.precios_acciones", ["ticker", "fecha"], rows)
    return len(rows), None


def run(filter_ticker: str | None = None, jr=None) -> None:
    tickers = _underlyings_activos(filter_ticker)
    logger.info("precios_acciones_daily — %d tickers", len(tickers))

    total = 0
    errors = 0
    fallidos: list[str] = []
    for t in tickers:
        n, err = upsert_ticker(t)
        if err:
            logger.warning("%-6s FAIL: %s", t, err)
            errors += 1
            fallidos.append(t)
            if jr:
                jr.error(f"{t}: {err}")
        else:
            logger.info("%-6s %d velas upserteadas", t, n)
            total += n
        time.sleep(0.3)  # anti rate-limit yfinance

    logger.info("Resumen: %d velas upserteadas · %d errores", total, errors)
    if jr:
        jr.set_stat("tickers", len(tickers))
        jr.set_stat("velas", total)
        jr.set_stat("errores", errors)
        # La lista, para el agente (§0.dk): `partial` todos los días por el
        # mismo ticker era un color, no un dato.
        jr.set_stat("errores_lista", fallidos)


def run_backfill(filter_ticker: str | None = None) -> None:
    """Historia completa desde DESDE_BACKFILL. Scopeado (REGLA #4): sin
    --ticker solo procesa las series que NO llegan a esa fecha; con --ticker
    fuerza ese aunque parezca completo. Batcheado por ticker + throttle.
    Idempotente: cortarlo y re-correrlo no rompe nada."""
    tickers = _underlyings_activos(filter_ticker)
    if not filter_ticker:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ticker, min(fecha) FROM mercado.precios_acciones "
                "WHERE ticker = ANY(%s) GROUP BY ticker",
                (tickers,),
            )
            inicio = dict(cur.fetchall())
        corte = (DESDE_BACKFILL + timedelta(days=15)).date()  # margen de feriados de enero
        tickers = [t for t in tickers if inicio.get(t) is None or inicio[t] > corte]
    logger.info("backfill precios_acciones — %d tickers (desde %s)",
                len(tickers), DESDE_BACKFILL.date())

    total = 0
    errors = 0
    for t in tickers:
        n, err = backfill_ticker(t)
        if err:
            logger.warning("%-6s FAIL: %s", t, err)
            errors += 1
        else:
            logger.info("%-6s %d velas upserteadas", t, n)
            total += n
        time.sleep(SLEEP_BACKFILL_S)

    logger.info("Backfill: %d velas upserteadas · %d errores", total, errors)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", help="Solo un ticker (debug / forzar backfill)")
    ap.add_argument("--backfill", action="store_true",
                    help="historia completa desde DESDE_BACKFILL para series incompletas")
    args = ap.parse_args()
    if args.backfill:
        run_backfill(filter_ticker=args.ticker)
    elif args.ticker:  # modo debug: no ensucia manager.job_runs
        run(filter_ticker=args.ticker)
    else:
        with JobRunLogger("precios_acciones_daily") as _jr:
            run(jr=_jr)
