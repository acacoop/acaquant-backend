"""snapshot_cierre.py — materializa el cierre diario por bono en Trading.SnapshotsCierre.

Lee directamente de Trading.MarketSnapshot al cierre. Como el motor de
mercado para a 17:05 ART y este cron corre 17:25 ART (20:25 UTC),
MarketSnapshot ya no recibe más writes del día — su estado representa
el cierre real (last_price, métricas analíticas, total_nominals).

Esto desacopla SnapshotsCierre de TimeSales: ya no agregamos trades del
día, leemos directo el último estado del MarketSnapshot. Los analíticos
(tea, tem, duration, paridad, etc.) los escribió curvas.py durante la
rueda; los de precio (last_price, total_nominals) los escribió valores.py.

GUARD contra feriados / días sin trades: si metrics.total_nominals == 0
o metrics.last_price == 0, skip — no persistir snapshot stale (los
analíticos pueden quedar congelados de cierres anteriores).

IDEMPOTENTE: re-correr el mismo día produce los mismos docs. Upsert por
(ts_cierre, curva, ticker).

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

from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("SnapshotCierre")

# Curvas cuyo cierre persistimos cada día leyendo MarketSnapshot. Soberanos
# (Hard Dólar) entró acá para alimentar la serie de Retorno Total: sin esto su
# histórico en SnapshotsCierre se cortaba y la vista quedaba con <2 puntos. No
# necesita el split globales/bonares por jurisdicción — `tipo` ya viene por ticker.
CURVAS_V1 = ("tasa_fija", "cer", "soberanos")


def _meta_curvas(client, curva: str) -> dict[str, dict]:
    """Lee metadata estática de Trading.Curvas por curva.
    ticker → {ticker_corto, tipo, fecha_vencimiento, fecha_emision, cupon_anual}."""
    out: dict[str, dict] = {}
    cur = client["Trading"]["Curvas"].find(
        {"curva": curva},
        {"_id": 0, "ticker": 1, "ticker_corto": 1, "tipo": 1,
         "fecha_vencimiento": 1, "fecha_emision": 1, "cupon_anual": 1},
    )
    for d in cur:
        if d.get("ticker"):
            out[d["ticker"]] = d
    return out


def _market_snapshots(client, tickers: list[str]) -> dict[str, dict]:
    """Lee el estado del cierre desde Trading.MarketSnapshot.
    ticker → {metrics: {last_price, total_nominals, TEA, TEM, duration,
              mod_duration, convexity, paridad}}."""
    out: dict[str, dict] = {}
    cur = client["Trading"]["MarketSnapshot"].find(
        {"ticker": {"$in": tickers}},
        {"_id": 0, "ticker": 1,
         "metrics.last_price": 1, "metrics.total_nominals": 1,
         "metrics.TEA": 1, "metrics.TEM": 1,
         "metrics.duration": 1, "metrics.mod_duration": 1,
         "metrics.convexity": 1, "metrics.paridad": 1},
    )
    for d in cur:
        if d.get("ticker"):
            out[d["ticker"]] = d
    return out


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


def procesar_curva(client, curva: str, fecha_str: str, dry: bool) -> int:
    """Procesa una curva leyendo MarketSnapshot.
    Skipea tickers sin trades del día (last_price == 0 o total_nominals == 0).
    Devuelve cantidad de docs upserteados."""
    metas = _meta_curvas(client, curva)
    if not metas:
        logger.warning("[%s] sin tickers en Trading.Curvas — saltando", curva)
        return 0

    tickers = list(metas.keys())
    snaps = _market_snapshots(client, tickers)
    if not snaps:
        logger.warning("[%s %s] sin docs en MarketSnapshot — saltando", curva, fecha_str)
        return 0

    col = client["Trading"]["SnapshotsCierre"]
    n_ok = 0
    n_skip = 0
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

        doc = {
            "ts_cierre":         fecha_str,
            "curva":             curva,
            "ticker":            ticker,
            "ticker_corto":      meta.get("ticker_corto"),
            "tipo":              meta.get("tipo"),
            "fecha_vencimiento": _norm_fecha(meta.get("fecha_vencimiento")),
            "fecha_emision":     _norm_fecha(meta.get("fecha_emision")),
            "ultimo_precio":     last_price,
            "tea":               _to_float(metrics.get("TEA")),
            "tem":               _to_float(metrics.get("TEM")),
            "paridad":           _to_float(metrics.get("paridad")),
            "duration":          _to_float(metrics.get("duration")),
            "mod_duration":      _to_float(metrics.get("mod_duration")),
            "convexity":         _to_float(metrics.get("convexity")),
            "total_nominals_dia": total_nominals,
            "is_zero_coupon":    is_zero_coupon if curva == "cer" else None,
        }
        if dry:
            n_ok += 1
            continue
        col.update_one(
            {"ts_cierre": fecha_str, "curva": curva, "ticker": ticker},
            {"$set": doc},
            upsert=True,
        )
        n_ok += 1

    logger.info("[%s %s] %d bonos persistidos (%d skipped)",
                curva, fecha_str, n_ok, n_skip)
    return n_ok


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

    client = get_mongo_client()

    # Indice único idempotente. Mongo lo crea solo la primera vez.
    client["Trading"]["SnapshotsCierre"].create_index(
        [("ts_cierre", 1), ("curva", 1), ("ticker", 1)],
        unique=True,
        name="uq_ts_curva_ticker",
    )

    from core.job_runs import JobRunLogger
    with JobRunLogger("snapshot_cierre") as jr:
        total = 0
        for curva in CURVAS_V1:
            total += procesar_curva(client, curva, fecha_str, args.dry)
        jr.set_stat("docs", total)
        jr.set_stat("fecha", fecha_str)
        jr.set_stat("dry", args.dry)
        logger.info("Total: %d docs en %s", total, fecha_str)
    if args.dry:
        logger.info("(--dry: no se escribió en Mongo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
