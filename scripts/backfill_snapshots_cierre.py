"""Backfill de Trading.SnapshotsCierre desde TimeSales (días pasados).

Para curvas que NO tienen cobertura en SnapshotsCierre (default:
soberanos, tamar, dolar_linked). El job actual `jobs/snapshot_cierre.py`
solo cubre el día corriente leyendo MarketSnapshot — para fechas pasadas
necesitamos agregar TimeSales con la lógica vieja.

Auto-detecta el rango por curva mirando trades enriquecidos en TimeSales.
Si una curva empezó a operar el 15/3, el backfill arranca ahí — sin
asumir fechas anteriores.

IDEMPOTENTE: skipea por default días que ya tienen snapshot (filtra por
ts_cierre existente). Con --force sobrescribe.

Uso:
    python -m scripts.backfill_snapshots_cierre --dry
    python -m scripts.backfill_snapshots_cierre              # apply
    python -m scripts.backfill_snapshots_cierre --curva soberanos
    python -m scripts.backfill_snapshots_cierre --desde 2026-04-01 --hasta 2026-04-30
    python -m scripts.backfill_snapshots_cierre --force
"""
from __future__ import annotations

import argparse
import logging
from datetime import UTC, date, datetime, timedelta

from pymongo import UpdateOne

from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_snapshots_cierre")

DB = "Trading"
COL_TS = "TimeSales"
COL_SC = "SnapshotsCierre"
COL_CURVAS = "Curvas"

CURVAS_DEFAULT = ("soberanos", "tamar", "dolar_linked")
CURVAS_VALIDAS = ("tasa_fija", "cer", "soberanos", "tamar", "dolar_linked")


def _meta_curva(client, curva: str) -> dict[str, dict]:
    """Metadata estática por ticker para la curva.
    ticker → {ticker_corto, tipo, fecha_vencimiento, fecha_emision, cupon_anual}."""
    out: dict[str, dict] = {}
    cur = client[DB][COL_CURVAS].find(
        {"curva": curva},
        {"_id": 0, "ticker": 1, "ticker_corto": 1, "tipo": 1,
         "fecha_vencimiento": 1, "fecha_emision": 1, "cupon_anual": 1},
    )
    for d in cur:
        if d.get("ticker"):
            out[d["ticker"]] = d
    return out


def _norm_fecha(v) -> str | None:
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


def _detectar_rango(client, tickers: list[str]) -> tuple[date | None, date | None]:
    """Primer y último día en TimeSales con trades enriquecidos para estos tickers.
    Usa índice ticker+duration+timestamp."""
    if not tickers:
        return None, None
    col = client[DB][COL_TS]
    primer = col.find_one(
        {"ticker": {"$in": tickers}, "duration": {"$exists": True, "$ne": None}},
        {"_id": 0, "timestamp": 1},
        sort=[("timestamp", 1)],
    )
    ultimo = col.find_one(
        {"ticker": {"$in": tickers}, "duration": {"$exists": True, "$ne": None}},
        {"_id": 0, "timestamp": 1},
        sort=[("timestamp", -1)],
    )
    if not primer or not ultimo:
        return None, None
    return primer["timestamp"].date(), ultimo["timestamp"].date()


def _fechas_existentes(client, curva: str) -> set[str]:
    """ts_cierre que ya están en SnapshotsCierre para esta curva."""
    cur = client[DB][COL_SC].find(
        {"curva": curva},
        {"_id": 0, "ts_cierre": 1},
    )
    return {d["ts_cierre"] for d in cur if d.get("ts_cierre")}


def _agregar_dia(client, tickers: list[str], dia: date) -> list[dict]:
    """Agrega TimeSales del día por ticker. Devuelve los rows con
    el último trade enriquecido + sum(size) del día."""
    inicio = datetime(dia.year, dia.month, dia.day, tzinfo=UTC)
    fin = inicio + timedelta(days=1)
    pipeline = [
        {"$match": {
            "ticker": {"$in": tickers},
            "timestamp": {"$gte": inicio, "$lt": fin},
            "price": {"$gt": 0},
        }},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id":            "$ticker",
            "price":          {"$first": "$price"},
            "TEA":            {"$first": "$TEA"},
            "TEM":            {"$first": "$TEM"},
            "paridad":        {"$first": "$paridad"},
            "duration":       {"$first": "$duration"},
            "mod_duration":   {"$first": "$mod_duration"},
            "convexity":      {"$first": "$convexity"},
            "ts":             {"$first": "$timestamp"},
            "total_nominals": {"$sum": "$size"},
        }},
    ]
    return list(client[DB][COL_TS].aggregate(pipeline))


def _build_doc(curva: str, fecha_str: str, ticker: str, meta: dict, agg_row: dict) -> dict:
    cupon = meta.get("cupon_anual")
    is_zc = (cupon is None) or (_to_float(cupon) == 0.0)
    return {
        "ts_cierre":         fecha_str,
        "curva":             curva,
        "ticker":            ticker,
        "ticker_corto":      meta.get("ticker_corto"),
        "tipo":              meta.get("tipo"),
        "fecha_vencimiento": _norm_fecha(meta.get("fecha_vencimiento")),
        "fecha_emision":     _norm_fecha(meta.get("fecha_emision")),
        "ultimo_precio":     _to_float(agg_row.get("price")),
        "tea":               _to_float(agg_row.get("TEA")),
        "tem":               _to_float(agg_row.get("TEM")),
        "paridad":           _to_float(agg_row.get("paridad")),
        "duration":          _to_float(agg_row.get("duration")),
        "mod_duration":      _to_float(agg_row.get("mod_duration")),
        "convexity":         _to_float(agg_row.get("convexity")),
        "total_nominals_dia": _to_float(agg_row.get("total_nominals")),
        "is_zero_coupon":    is_zc if curva == "cer" else None,
    }


def procesar_curva(
    client, curva: str, desde_arg: date | None, hasta_arg: date | None,
    force: bool, dry: bool,
) -> tuple[int, int]:
    """Procesa una curva. Devuelve (docs_upserted, dias_skipped)."""
    metas = _meta_curva(client, curva)
    if not metas:
        logger.warning("[%s] sin tickers en Trading.Curvas — saltando", curva)
        return 0, 0

    tickers = list(metas.keys())

    # Auto-detect del rango si no vienen overrideados.
    if desde_arg and hasta_arg:
        primer = desde_arg
        ultimo = hasta_arg
    else:
        primer_d, ultimo_d = _detectar_rango(client, tickers)
        if not primer_d or not ultimo_d:
            logger.warning("[%s] sin trades enriquecidos en TimeSales — saltando", curva)
            return 0, 0
        primer = desde_arg or primer_d
        ultimo = hasta_arg or ultimo_d

    logger.info("[%s] %d tickers · rango %s → %s", curva, len(tickers), primer, ultimo)

    existentes = set() if force else _fechas_existentes(client, curva)
    if existentes:
        logger.info("[%s] %d días ya cubiertos en SnapshotsCierre — skip (usá --force para reescribir)",
                    curva, len(existentes))

    col_sc = client[DB][COL_SC]
    docs_total = 0
    dias_skipped = 0
    dia = primer
    while dia <= ultimo:
        fecha_str = dia.isoformat()
        if fecha_str in existentes:
            dia += timedelta(days=1)
            continue

        rows = _agregar_dia(client, tickers, dia)
        if not rows:
            # Día sin trades para esta curva (probablemente no hábil o feriado).
            dias_skipped += 1
            dia += timedelta(days=1)
            continue

        ops = []
        for r in rows:
            ticker = r["_id"]
            meta = metas.get(ticker)
            if not meta:
                continue
            doc = _build_doc(curva, fecha_str, ticker, meta, r)
            if dry:
                continue
            ops.append(UpdateOne(
                {"ts_cierre": fecha_str, "curva": curva, "ticker": ticker},
                {"$set": doc},
                upsert=True,
            ))

        if ops and not dry:
            col_sc.bulk_write(ops, ordered=False)
        docs_total += len(rows)
        logger.info("  %s · %d bonos", fecha_str, len(rows))
        dia += timedelta(days=1)

    logger.info("[%s] backfill: %d docs · %d días sin trades",
                curva, docs_total, dias_skipped)
    return docs_total, dias_skipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--curva",
                        help=f"curva específica (default: {','.join(CURVAS_DEFAULT)})")
    parser.add_argument("--desde", help="YYYY-MM-DD (override auto-detect)")
    parser.add_argument("--hasta", help="YYYY-MM-DD (override auto-detect)")
    parser.add_argument("--force", action="store_true",
                        help="sobrescribir días ya cubiertos")
    parser.add_argument("--dry", action="store_true",
                        help="preview, no escribe")
    args = parser.parse_args()

    if args.curva:
        if args.curva not in CURVAS_VALIDAS:
            raise SystemExit(f"--curva inválida: {args.curva!r} ∉ {CURVAS_VALIDAS}")
        curvas = (args.curva,)
    else:
        curvas = CURVAS_DEFAULT

    desde_arg = date.fromisoformat(args.desde) if args.desde else None
    hasta_arg = date.fromisoformat(args.hasta) if args.hasta else None

    client = get_mongo_client()

    # Index único por consistencia con el job principal.
    client[DB][COL_SC].create_index(
        [("ts_cierre", 1), ("curva", 1), ("ticker", 1)],
        unique=True,
        name="uq_ts_curva_ticker",
    )

    total = 0
    for curva in curvas:
        n, _ = procesar_curva(client, curva, desde_arg, hasta_arg, args.force, args.dry)
        total += n

    logger.info("Total docs procesados: %d", total)
    if args.dry:
        logger.info("(--dry: nada escrito)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
