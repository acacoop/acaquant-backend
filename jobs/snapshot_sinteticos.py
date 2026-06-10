"""snapshot_sinteticos.py — materializa el cierre diario de sintéticos en Trading.SnapshotsSinteticos.

Espeja el patrón de jobs.snapshot_cierre, pero para las tasas sintéticas
(LECAP+Rofex largo / DLK+Rofex corto). Reusa el service get_sinteticos()
(misma excepción de capa que pnl_totales_precompute / consolidado_cuentas:
un job de precompute puede reusar un service de api/).

Como el cron corre post-cierre (motores parados 20:05 UTC), get_sinteticos()
lee de MarketSnapshot/FuturosDLRSnapshot el último estado del día → es el
cierre real de cada sintético. Acumula la serie histórica para poder graficar
TNA/TE/precios en el tiempo (hoy sólo existía el live).

GUARD: si una fila no tiene tna NI te (sin precio válido), se saltea — no
ensucia la serie con None.

IDEMPOTENTE: re-correr el mismo día produce los mismos docs. Upsert por
(ts_snapshot, tipo_sintetico, ticker).

Uso:
    python -m jobs.snapshot_sinteticos              # cierre del día UTC actual
    python -m jobs.snapshot_sinteticos --fecha 2026-06-10
    python -m jobs.snapshot_sinteticos --dry        # no persiste, imprime resumen
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, date, datetime

from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("SnapshotSinteticos")


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm_fecha(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    s = str(v)
    return s[:10] if len(s) >= 10 else None


def _doc_lecap(row: dict) -> dict:
    """Fila de long_rofex_long_lecap → doc normalizado."""
    return {
        "ticker_largo":    row.get("ticker_largo"),
        "futuro_ticker":   row.get("futuro_ticker"),
        "px_instrumento":  _to_float(row.get("px_tf")),       # precio LECAP (ARS)
        "px_futuro":       _to_float(row.get("px_futuro")),
        "vto_instrumento": _norm_fecha(row.get("vto_fecha")),
        "vto_futuro":      _norm_fecha(row.get("futuro_vto_fecha")),
        "plazo_normal":    row.get("plazo_normal"),
        "descalce":        row.get("descalce"),
        "te":              _to_float(row.get("te")),
        "tna":             _to_float(row.get("tna")),
        # específicos del armado LECAP
        "cobro":           _to_float(row.get("cobro")),
        "t0":              _to_float(row.get("t0")),
        "tn":              _to_float(row.get("tn")),
    }


def _doc_dlk(row: dict) -> dict:
    """Fila de short_rofex_long_dlk → doc normalizado."""
    return {
        "ticker_largo":    row.get("ticker_largo"),
        "futuro_ticker":   row.get("futuro_ticker"),
        "px_instrumento":  _to_float(row.get("px_dlk")),      # paridad DLK (por 100 VN)
        "px_futuro":       _to_float(row.get("px_futuro")),
        "vto_instrumento": _norm_fecha(row.get("vto_dlk")),
        "vto_futuro":      _norm_fecha(row.get("vto_futuro")),
        "plazo_normal":    row.get("plazo_normal"),
        "descalce":        row.get("descalce"),
        "te":              _to_float(row.get("te")),
        "tna":             _to_float(row.get("tna")),
        # específicos del armado DLK
        "dolar_emision":   _to_float(row.get("dolar_emision")),
    }


# tipo_sintetico → (clave en el dict de get_sinteticos, builder de doc)
_FAMILIAS = {
    "long_lecap": ("long_rofex_long_lecap", _doc_lecap),
    "short_dlk":  ("short_rofex_long_dlk", _doc_dlk),
}


def procesar(client, fecha_str: str, dry: bool) -> int:
    """Lee get_sinteticos() y persiste un doc por (familia, ticker).
    Skipea filas sin tna NI te (sin precio válido). Devuelve docs upserteados."""
    from api.services.sinteticos import get_sinteticos

    data = get_sinteticos()
    spot = _to_float(data.get("spot"))
    spot_source = data.get("spot_source")
    spot_ts = data.get("spot_ts")

    col = client["Trading"]["SnapshotsSinteticos"]
    n_ok = 0
    n_skip = 0
    for tipo_sintetico, (clave, builder) in _FAMILIAS.items():
        for row in data.get(clave) or []:
            ticker = row.get("ticker")
            if not ticker:
                n_skip += 1
                continue
            base = builder(row)
            # GUARD: sin tna ni te no hay sintético medible → no persistir.
            if base.get("tna") is None and base.get("te") is None:
                n_skip += 1
                continue

            doc = {
                "ts_snapshot":    fecha_str,
                "tipo_sintetico": tipo_sintetico,
                "ticker":         ticker,
                "spot":           spot,
                "spot_source":    spot_source,
                "spot_ts":        spot_ts,
                **base,
            }
            if dry:
                n_ok += 1
                continue
            col.update_one(
                {"ts_snapshot": fecha_str, "tipo_sintetico": tipo_sintetico, "ticker": ticker},
                {"$set": doc},
                upsert=True,
            )
            n_ok += 1

    logger.info("[%s] %d sintéticos persistidos (%d skipped)", fecha_str, n_ok, n_skip)
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

    # Índice único idempotente. Mongo lo crea solo la primera vez.
    client["Trading"]["SnapshotsSinteticos"].create_index(
        [("ts_snapshot", 1), ("tipo_sintetico", 1), ("ticker", 1)],
        unique=True,
        name="uq_ts_tipo_ticker",
    )

    from core.job_runs import JobRunLogger
    with JobRunLogger("snapshot_sinteticos") as jr:
        total = procesar(client, fecha_str, args.dry)
        jr.set_stat("docs", total)
        jr.set_stat("fecha", fecha_str)
        jr.set_stat("dry", args.dry)
        logger.info("Total: %d docs en %s", total, fecha_str)
    if args.dry:
        logger.info("(--dry: no se escribió en Mongo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
