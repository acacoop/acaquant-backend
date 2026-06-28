"""snapshot_cierre.py — materializa el cierre diario por bono SQL-NATIVE.

Lee directamente de Trading.MarketSnapshot al cierre. Como el motor de
mercado para a 17:05 ART y este cron corre 17:25 ART (20:25 UTC),
MarketSnapshot ya no recibe más writes del día — su estado representa
el cierre real (last_price, métricas analíticas, total_nominals).

Esto desacopla el cierre de TimeSales: ya no agregamos trades del día,
leemos directo el último estado del MarketSnapshot. Los analíticos
(tea, tem, duration, paridad, etc.) los escribió curvas.py durante la
rueda; los de precio (last_price, total_nominals) los escribió valores.py.

SQL-NATIVE (cutover 2026-06-24): `Trading.SnapshotsCierre` (Mongo) fue migrada
→ dropeada. Este job escribe DOS tablas Postgres (write_native, incondicional):
  • `mercado.snapshots_cierre_hist` — HISTÓRICO por (fecha, curva, ticker).
  • `mercado.snapshots_cierre`      — ÚLTIMO precio por ticker (ticker,
    last_price, fecha). Es el FALLBACK DE PRECIO del PnL (api/services/pnl_sql).
    El job corre por el día actual → upsertear (ticker, hoy) deja siempre el
    cierre más reciente como "último". Antes esta tabla la derivaba el sync
    desde Mongo (sync_snapshots_cierre, eliminado en el cutover).

GUARD contra feriados / días sin trades: si metrics.total_nominals == 0
o metrics.last_price == 0, skip — no persistir snapshot stale (los
analíticos pueden quedar congelados de cierres anteriores).

IDEMPOTENTE: re-correr el mismo día produce las mismas filas. Upsert por
(fecha, curva, ticker) en hist y por (ticker) en el último-precio.

Uso:
    python -m jobs.snapshot_cierre              # cierre del día UTC actual
    python -m jobs.snapshot_cierre --fecha 2026-04-25
    python -m jobs.snapshot_cierre --dry        # no persiste, imprime resumen
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, date, datetime

from core.pg_mirror import write_native

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("SnapshotCierre")

# Curvas cuyo cierre persistimos cada día leyendo MarketSnapshot. Soberanos
# (Hard Dólar) entró acá para alimentar la serie de Retorno Total: sin esto su
# histórico en SnapshotsCierre se cortaba y la vista quedaba con <2 puntos. No
# necesita el split globales/bonares por jurisdicción — `tipo` ya viene por ticker.
CURVAS_V1 = ("tasa_fija", "cer", "soberanos")


def _meta_curvas(curva: str) -> dict[str, dict]:
    """Lee metadata estática del master por curva desde mercado.curvas (SQL).
    ticker → doc completo (ticker_corto, tipo, fecha_vencimiento, fecha_emision,
    cupon_anual, …)."""
    from core import curvas_sql
    return {d["ticker"]: d for d in curvas_sql.por_curva(curva) if d.get("ticker")}


def _market_snapshots(tickers: list[str]) -> dict[str, dict]:
    """Estado del cierre — SQL-only (mercado.market_snapshot). ticker →
    {ticker, metrics: {last_price, total_nominals, TEA, TEM, duration,
    mod_duration, convexity, paridad}, book, updated_at}."""
    from core.market_snapshot import snapshot_docs
    return snapshot_docs(tickers)


def _norm_fecha(v) -> str | None:
    """Normaliza fecha (datetime o str) a 'YYYY-MM-DD' o None."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    s = str(v)
    return s[:10] if len(s) >= 10 else None


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _write_ultimo_precio(rows: list[dict]) -> int:
    """Upsert GUARDADO de `mercado.snapshots_cierre` (último precio por ticker).

    Sólo avanza la fila si la `fecha` nueva es >= la persistida (`WHERE EXCLUDED.fecha
    >= snapshots_cierre.fecha`). Replica la semántica del viejo sync (último por MAX
    ts_cierre): así un backfill `--fecha` de un día VIEJO no pisa el cierre más reciente
    que ya alimenta el fallback de precio del PnL. Best-effort: nunca levanta."""
    if not rows:
        return 0
    try:
        from core.postgres import get_pool
        vals = [(r["ticker"], r["last_price"], r["fecha"]) for r in rows]
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO mercado.snapshots_cierre (ticker, last_price, fecha) "
                "VALUES (%s, %s, %s) ON CONFLICT (ticker) DO UPDATE SET "
                "last_price = EXCLUDED.last_price, fecha = EXCLUDED.fecha "
                "WHERE mercado.snapshots_cierre.fecha IS NULL "
                "OR EXCLUDED.fecha >= mercado.snapshots_cierre.fecha",
                vals,
            )
        return len(vals)
    except Exception as e:
        logger.error("snapshots_cierre (último) falló: %s", str(e).splitlines()[0][:200])
        return 0


def _pg_date(v: str | None) -> date | None:
    """'YYYY-MM-DD' (output de _norm_fecha) → date para el espejo SQL."""
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def procesar_curva(
    curva: str, fecha_str: str, dry: bool,
) -> tuple[int, list[dict], list[dict]]:
    """Procesa una curva leyendo MarketSnapshot.
    Skipea tickers sin trades del día (last_price == 0 o total_nominals == 0).
    Devuelve (n_ok, filas_hist, filas_last) — las filas para las dos tablas SQL:
    `mercado.snapshots_cierre_hist` (histórico) y `mercado.snapshots_cierre`
    (último precio por ticker, fallback del PnL). El caller las escribe juntas."""
    metas = _meta_curvas(curva)
    if not metas:
        logger.warning("[%s] sin tickers en mercado.curvas — saltando", curva)
        return 0, [], []

    tickers = list(metas.keys())
    snaps = _market_snapshots(tickers)
    if not snaps:
        logger.warning("[%s %s] sin docs en MarketSnapshot — saltando", curva, fecha_str)
        return 0, [], []

    n_ok = 0
    n_skip = 0
    hist_rows: list[dict] = []
    last_rows: list[dict] = []
    fecha_d = date.fromisoformat(fecha_str)
    for ticker, meta in metas.items():
        snap = snaps.get(ticker)
        if not snap:
            n_skip += 1
            continue
        metrics = snap.get("metrics") or {}
        last_price = _to_float(metrics.get("last_price"))
        total_nominals = _to_float(metrics.get("total_nominals"))

        # GUARD: feriados / días sin trades — last_price == 0 o
        # total_nominals == 0 indica que el motor arrancó pero no recibió
        # ningún trade. Las analíticas (TEA/duration/etc) que están en el
        # doc son del cierre anterior — NO persistir como cierre del día
        # actual o ensuciamos la serie histórica.
        if not last_price or not total_nominals:
            n_skip += 1
            continue

        cupon = meta.get("cupon_anual")
        is_zero_coupon = (cupon is None) or (_to_float(cupon) == 0.0)

        n_ok += 1
        if dry:
            continue
        # Fila del HISTÓRICO (mercado.snapshots_cierre_hist; ts_cierre → fecha date,
        # strings de fecha → date). Ver sql/schema.sql §CAPA MERCADO.
        hist_rows.append({
            "fecha": fecha_d, "curva": curva, "ticker": ticker,
            "ticker_corto": meta.get("ticker_corto"), "tipo": meta.get("tipo"),
            "fecha_vencimiento": _pg_date(_norm_fecha(meta.get("fecha_vencimiento"))),
            "fecha_emision": _pg_date(_norm_fecha(meta.get("fecha_emision"))),
            "ultimo_precio": last_price,
            "tea": _to_float(metrics.get("TEA")), "tem": _to_float(metrics.get("TEM")),
            "paridad": _to_float(metrics.get("paridad")),
            "duration": _to_float(metrics.get("duration")),
            "mod_duration": _to_float(metrics.get("mod_duration")),
            "convexity": _to_float(metrics.get("convexity")),
            "total_nominals_dia": total_nominals,
            "is_zero_coupon": is_zero_coupon if curva == "cer" else None,
        })
        # Fila del ÚLTIMO precio por ticker (mercado.snapshots_cierre, fallback del PnL).
        # El job corre por el día actual → upsertear (ticker, hoy) deja el cierre más
        # reciente como "último". Antes lo derivaba sync_snapshots_cierre desde Mongo.
        last_rows.append({"ticker": ticker, "last_price": last_price, "fecha": fecha_d})

    logger.info("[%s %s] %d bonos persistidos (%d skipped)",
                curva, fecha_str, n_ok, n_skip)
    return n_ok, hist_rows, last_rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fecha", help="YYYY-MM-DD (default: hoy UTC)")
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    if args.fecha:
        try:
            fecha_d = date.fromisoformat(args.fecha)
        except ValueError:
            raise SystemExit(f"--fecha inválida: {args.fecha}") from None
    else:
        fecha_d = datetime.now(UTC).date()
    fecha_str = fecha_d.isoformat()

    # Lectura 100% SQL (mercado.market_snapshot + mercado.curvas). El cierre se
    # escribe SQL-native — ya no se toca Mongo.
    from core.job_runs import JobRunLogger
    with JobRunLogger("snapshot_cierre") as jr:
        total = 0
        hist_rows: list[dict] = []
        last_rows: list[dict] = []
        for curva in CURVAS_V1:
            n, h_rows, l_rows = procesar_curva(curva, fecha_str, args.dry)
            total += n
            hist_rows.extend(h_rows)
            last_rows.extend(l_rows)
        if not args.dry:
            # Histórico: upsert incondicional por (fecha, curva, ticker).
            write_native("mercado.snapshots_cierre_hist", ["fecha", "curva", "ticker"],
                         hist_rows)
            # Último-por-ticker: upsert guardado (sólo avanza fecha) — fallback del PnL.
            _write_ultimo_precio(last_rows)
        jr.set_stat("docs", total)
        jr.set_stat("fecha", fecha_str)
        jr.set_stat("dry", args.dry)
        logger.info("Total: %d docs en %s", total, fecha_str)
    if args.dry:
        logger.info("(--dry: no se escribió en SQL)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
