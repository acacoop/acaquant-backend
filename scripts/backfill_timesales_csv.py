"""Backfill histórico de Trading.TimeSales desde un CSV con cierres diarios.

Soporta dos formatos:

1) **Single-ticker** — 2 columnas (fecha, close), un único bono:
       fecha,close
       2024-01-15,75.50
       2024-01-16,75.20
   Se necesita pasar --ticker-corto o --ticker.

2) **Wide / multi-ticker** — una columna por ticker, primer columna fecha:
       fecha;AL30C;GD30D;GD29D;GD35D;GD41D;GD38D
       2024-01-15;...;75.50;...;...;...;...
   Detección automática: si los headers matchean ticker_cortos
   conocidos. NO requiere --ticker; cada columna se procesa con su
   propio ticker (resuelto en Trading.Curvas si existe, sino se asume
   "MERV - XMEV - <SIMBOLO> - 24hs").

Auto-detecta separador (`,` o `;`).

Shape de cada doc insertado (idéntico al que escribe motor_rofex en
trades reales, sin los analíticos):
    {ticker, timestamp, price, size, side, money}

motor_curvas en su próximo ciclo agrega TEA / TEM / duration /
convexity / paridad como si fueran trades live.

Idempotente: UpdateOne con upsert por (ticker, timestamp). Re-correr
no duplica.

Uso:
    # Single-ticker
    python -m scripts.backfill_timesales_csv \\
        --csv docs/soberanos/GD30D_historico.csv --ticker-corto GD30D

    # Wide (multi-ticker, sin --ticker)
    python -m scripts.backfill_timesales_csv \\
        --csv docs/soberanos/backfillglobales.csv

    # Solo procesar algunos tickers del wide
    python -m scripts.backfill_timesales_csv \\
        --csv docs/soberanos/backfillglobales.csv --solo GD30D,GD35D

    # Preview sin escribir
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
    """YYYY-MM-DD, DD/MM/YYYY, ISO, etc. Devuelve datetime UTC naive."""
    s = (s or "").strip()
    if not s:
        return None
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


def _parse_close(s: str) -> float | None:
    s = (s or "").strip().replace(",", ".")
    if not s or s in ("-", "—", "N/A", "n/a", "NA"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def _detectar_separador(first_line: str) -> str:
    """Si hay más ';' que ',' asume ';' (formato europeo / Reuters)."""
    return ";" if first_line.count(";") > first_line.count(",") else ","


def _resolver_ticker_full(client, ticker: str | None, ticker_corto: str | None) -> str:
    if ticker:
        return ticker
    if not ticker_corto:
        raise SystemExit("Pasá --ticker o --ticker-corto.")
    doc = client["Trading"]["Curvas"].find_one(
        {"ticker_corto": ticker_corto}, {"_id": 0, "ticker": 1},
    )
    if doc and doc.get("ticker"):
        return doc["ticker"]
    # Fallback: armar ticker estándar MERV.
    return f"MERV - XMEV - {ticker_corto} - 24hs"


def _resolver_tickers_desde_curvas(client, ticker_cortos: list[str]) -> dict[str, str]:
    """Mapea cada ticker_corto a su ticker completo. Si no está en Trading.Curvas
    lo arma con el formato estándar MERV."""
    docs = list(client["Trading"]["Curvas"].find(
        {"ticker_corto": {"$in": ticker_cortos}},
        {"_id": 0, "ticker": 1, "ticker_corto": 1},
    ))
    mapeo = {d["ticker_corto"]: d["ticker"] for d in docs if d.get("ticker_corto") and d.get("ticker")}
    for tc in ticker_cortos:
        mapeo.setdefault(tc, f"MERV - XMEV - {tc} - 24hs")
    return mapeo


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Path del CSV")
    parser.add_argument("--ticker", help='Ticker ROFEX completo (modo single)')
    parser.add_argument("--ticker-corto", help="Modo single: ticker corto (resuelve via Curvas)")
    parser.add_argument("--solo", help="Modo wide: filtrar a estos ticker_cortos (CSV)")
    parser.add_argument("--side", default="MID")
    parser.add_argument("--size", type=float, default=1.0)
    parser.add_argument("--hora-utc", default="20:00",
                        help="Hora UTC del cierre (default 20:00 = 17:00 ART)")
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        raise SystemExit(f"CSV no existe: {csv_path}")

    with csv_path.open(encoding="utf-8-sig") as f:
        first_line = f.readline()
    sep = _detectar_separador(first_line)

    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=sep)
        rows = list(reader)
    if len(rows) < 2:
        raise SystemExit(f"CSV vacío o solo header: {csv_path}")

    headers = [h.strip() for h in rows[0]]
    headers_norm = [h.lower() for h in headers]
    data_rows = rows[1:]

    client = get_mongo_client()
    hora_cierre = _parse_hora(args.hora_utc)

    # Detección de modo
    candidatos_close = ("close", "cierre", "precio", "price", "last")
    candidatos_fecha = ("fecha", "date", "")  # primera columna sin nombre = fecha
    idx_close = next((i for i, h in enumerate(headers_norm) if h in candidatos_close), None)

    es_wide = (idx_close is None and len(headers) >= 2)

    if es_wide:
        # Modo wide: primera columna = fecha, resto = un ticker por columna.
        idx_fecha = 0
        # Headers de tickers (ignora vacíos y columnas que matchean 'fecha')
        col_tickers: list[tuple[int, str]] = []
        for i, h in enumerate(headers):
            if i == idx_fecha:
                continue
            tc = h.strip().upper()
            if not tc:
                continue
            col_tickers.append((i, tc))

        if not col_tickers:
            raise SystemExit(f"Modo wide pero no encuentro columnas con tickers. Headers: {headers}")

        if args.solo:
            filtro = {t.strip().upper() for t in args.solo.split(",") if t.strip()}
            col_tickers = [(i, tc) for i, tc in col_tickers if tc in filtro]
            if not col_tickers:
                raise SystemExit(f"--solo no matchea ningún header: {args.solo}")

        ticker_full_map = _resolver_tickers_desde_curvas(
            client, [tc for _, tc in col_tickers],
        )

        print(f"Modo:      WIDE (separator={sep!r})")
        print(f"Tickers:   {[tc for _, tc in col_tickers]}")
        print(f"CSV:       {csv_path} ({len(data_rows)} filas)\n")

        ops: list[UpdateOne] = []
        skipped = 0
        primer_ts: datetime | None = None
        ultimo_ts: datetime | None = None

        for n, row in enumerate(data_rows, start=2):
            if not row:
                continue
            fecha_dt = _parse_fecha(row[idx_fecha] if idx_fecha < len(row) else "")
            if not fecha_dt:
                skipped += 1
                continue
            ts = datetime.combine(fecha_dt.date(), hora_cierre, tzinfo=UTC)
            primer_ts = ts if primer_ts is None or ts < primer_ts else primer_ts
            ultimo_ts = ts if ultimo_ts is None or ts > ultimo_ts else ultimo_ts

            for col_idx, tc in col_tickers:
                if col_idx >= len(row):
                    continue
                close = _parse_close(row[col_idx])
                if close is None:
                    continue
                ticker_full = ticker_full_map[tc]
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

    else:
        # Modo single
        if idx_close is None:
            raise SystemExit(
                f"No encontré columna de close en headers: {headers}. "
                f"Para wide, primera columna debe ser fecha y el resto tickers."
            )
        idx_fecha = next((i for i, h in enumerate(headers_norm) if h in candidatos_fecha and h), None)
        if idx_fecha is None:
            idx_fecha = 0  # asumir primera columna fecha
        ticker_full = _resolver_ticker_full(client, args.ticker, args.ticker_corto)

        print(f"Modo:      SINGLE (separator={sep!r})")
        print(f"Ticker:    {ticker_full}")
        print(f"CSV:       {csv_path} ({len(data_rows)} filas)\n")

        ops = []
        skipped = 0
        primer_ts = None
        ultimo_ts = None
        for n, row in enumerate(data_rows, start=2):
            if not row:
                continue
            fecha_dt = _parse_fecha(row[idx_fecha] if idx_fecha < len(row) else "")
            if not fecha_dt:
                skipped += 1
                continue
            close = _parse_close(row[idx_close] if idx_close < len(row) else "")
            if close is None:
                skipped += 1
                continue
            ts = datetime.combine(fecha_dt.date(), hora_cierre, tzinfo=UTC)
            primer_ts = ts if primer_ts is None or ts < primer_ts else primer_ts
            ultimo_ts = ts if ultimo_ts is None or ts > ultimo_ts else ultimo_ts

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

    print(f"Filas OK:  {len(ops)} docs · skipeadas {skipped}")
    if primer_ts and ultimo_ts:
        print(f"Rango:     {primer_ts.date()} → {ultimo_ts.date()}")
    print(f"Side: {args.side} · Size: {args.size} · Hora cierre UTC: {args.hora_utc}")

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
    print(f"\nOK: {nuevos} nuevos · {matched} ya existían (idempotente).")
    print("motor_curvas los va enriqueciendo en sus próximos ciclos (~5 s cada batch de 200).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
