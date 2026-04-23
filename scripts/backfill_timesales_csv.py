"""Backfill histórico de Trading.TimeSales desde un CSV con cierre diario.

CSV formato (case-insensitive en headers):
    fecha,close
    2024-01-15,75.50
    2024-01-16,75.20
    ...

Soporta fechas en YYYY-MM-DD o DD/MM/YYYY.

Inserta un doc por fila con el mismo shape que escribe motor_rofex en
trades reales — los campos directos, sin los analíticos:
    {
        ticker:    "MERV - XMEV - <SIMBOLO> - 24hs",
        timestamp: <fecha CSV> + hora cierre (default 20:00 UTC = 17:00 ART),
        price:     <close del CSV>,
        size:      1            # default; el cierre histórico no tiene volumen real
        side:      "MID"        # default; cierre no tiene side
        money:     price * size
    }

motor_curvas en su próximo ciclo (5 s) los detecta y agrega
TEA / TEM / duration / convexity / paridad — los procesa exactamente
igual que un trade live.

Idempotencia: usa UpdateOne con upsert por (ticker, timestamp). Si la
fila ya existe la skipea silenciosamente; podés re-correr sin duplicar.

Uso:
    python -m scripts.backfill_timesales_csv \\
        --csv docs/soberanos/GD29D_historico.csv \\
        --ticker-corto GD29D

    # ticker completo opcional
    python -m scripts.backfill_timesales_csv \\
        --csv docs/soberanos/GD29D_historico.csv \\
        --ticker "MERV - XMEV - GD29D - 24hs"

    # cambiar hora del cierre (default 20:00 UTC)
    python -m scripts.backfill_timesales_csv ... --hora-utc 21:00

    # preview sin escribir
    python -m scripts.backfill_timesales_csv ... --dry
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import UTC, datetime, time
from pathlib import Path

from pymongo import UpdateOne

from core.mongo import get_mongo_client


def _parse_fecha(s: str) -> datetime | None:
    """YYYY-MM-DD, DD/MM/YYYY, o ISO con T. Devuelve datetime UTC naive."""
    s = (s or "").strip()
    if not s:
        return None
    # Cortar parte de hora si viene
    if "T" in s:
        s = s.split("T", 1)[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _parse_hora(s: str) -> time:
    h, m = s.strip().split(":", 1)
    return time(int(h), int(m), 0)


def _resolver_ticker_full(client, ticker: str | None, ticker_corto: str | None) -> str:
    if ticker:
        return ticker
    if not ticker_corto:
        raise SystemExit("Pasá --ticker o --ticker-corto.")
    doc = client["Trading"]["Curvas"].find_one(
        {"ticker_corto": ticker_corto}, {"_id": 0, "ticker": 1},
    )
    if not doc or not doc.get("ticker"):
        raise SystemExit(
            f"No existe ticker_corto={ticker_corto!r} en Trading.Curvas. "
            f"Seedealo primero o pasá el --ticker completo."
        )
    return doc["ticker"]


def _detectar_cols(headers: list[str]) -> tuple[int, int]:
    """Devuelve (idx_fecha, idx_close)."""
    norm = [h.strip().lower() for h in headers]
    candidatos_fecha = ("fecha", "date")
    candidatos_close = ("close", "cierre", "precio", "price")
    idx_fecha = next((i for i, h in enumerate(norm) if h in candidatos_fecha), None)
    idx_close = next((i for i, h in enumerate(norm) if h in candidatos_close), None)
    if idx_fecha is None or idx_close is None:
        raise SystemExit(
            f"No encontré columnas 'fecha' y 'close' en el header: {headers}"
        )
    return idx_fecha, idx_close


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Path del CSV")
    parser.add_argument("--ticker", help='Ticker ROFEX completo (ej. "MERV - XMEV - GD29D - 24hs")')
    parser.add_argument("--ticker-corto", help="Alternativa: lo resuelve via Trading.Curvas")
    parser.add_argument("--side", default="MID", help="Lado del trade (default MID)")
    parser.add_argument("--size", type=float, default=1.0, help="Size por trade (default 1)")
    parser.add_argument("--hora-utc", default="20:00",
                        help="Hora UTC del cierre (default 20:00 = 17:00 ART)")
    parser.add_argument("--dry", action="store_true",
                        help="No escribe en Mongo, solo cuenta y muestra ejemplos")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        raise SystemExit(f"CSV no existe: {csv_path}")

    client = get_mongo_client()
    ticker_full = _resolver_ticker_full(client, args.ticker, args.ticker_corto)
    hora_cierre = _parse_hora(args.hora_utc)

    # Leer CSV
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if len(rows) < 2:
        raise SystemExit(f"CSV vacío o solo header: {csv_path}")

    headers = rows[0]
    idx_fecha, idx_close = _detectar_cols(headers)
    data_rows = rows[1:]

    ops: list[UpdateOne] = []
    skipped = 0
    primer_ts: datetime | None = None
    ultimo_ts: datetime | None = None

    for n, row in enumerate(data_rows, start=2):
        if not row or all(not c.strip() for c in row):
            continue
        fecha_dt = _parse_fecha(row[idx_fecha])
        if not fecha_dt:
            print(f"[skip fila {n}] fecha no parseable: {row[idx_fecha]!r}")
            skipped += 1
            continue
        try:
            close = float(row[idx_close].replace(",", "."))
        except (ValueError, IndexError):
            print(f"[skip fila {n}] close no parseable: {row[idx_close]!r}")
            skipped += 1
            continue
        if close <= 0:
            print(f"[skip fila {n}] close <= 0: {close}")
            skipped += 1
            continue

        ts = datetime.combine(fecha_dt.date(), hora_cierre, tzinfo=UTC)
        if primer_ts is None or ts < primer_ts:
            primer_ts = ts
        if ultimo_ts is None or ts > ultimo_ts:
            ultimo_ts = ts

        money = round(close * args.size, 6)
        doc = {
            "ticker":    ticker_full,
            "timestamp": ts,
            "price":     close,
            "size":      args.size,
            "side":      args.side,
            "money":     money,
        }
        ops.append(UpdateOne(
            {"ticker": ticker_full, "timestamp": ts},
            {"$setOnInsert": doc},
            upsert=True,
        ))

    print(f"\nTicker:    {ticker_full}")
    print(f"CSV:       {csv_path}  ({len(data_rows)} filas data)")
    print(f"Filas OK:  {len(ops)}  (skipeadas {skipped})")
    if primer_ts and ultimo_ts:
        print(f"Rango:     {primer_ts.date()} → {ultimo_ts.date()}")
    print(f"Side:      {args.side}  ·  Size: {args.size}  ·  Hora cierre UTC: {args.hora_utc}")

    if not ops:
        print("\nNada para insertar.")
        return 1

    if args.dry:
        print("\n--dry: NO se escribe en Mongo. Ejemplo de doc a insertar:")
        sample = ops[0]._doc["$setOnInsert"]
        for k, v in sample.items():
            print(f"  {k}: {v!r}")
        return 0

    col = client["Trading"]["TimeSales"]
    res = col.bulk_write(ops, ordered=False)
    nuevos = len(getattr(res, "upserted_ids", {}) or {})
    matched = getattr(res, "matched_count", 0)
    print(f"\nOK: {nuevos} nuevos · {matched} ya existían (skip por idempotencia).")
    print("motor_curvas los enriquecerá en el próximo ciclo (~5 s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
