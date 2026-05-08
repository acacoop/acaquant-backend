"""export_order_book_l2.py — vuelca Trading.OrderBookL2 a CSV.

Antes de dropear la colección (pendiente con el deprecate del motor
order_book L2 + MM Cartea), exportamos lo que hay capturado hasta hoy
para mantener el histórico fuera de Mongo.

Genera un archivo CSV por ticker en el directorio de output:
  order_book_l2_<ticker_safe>_<YYYYMMDD-HHMMSS>.csv

Cada fila = un cambio del book persistido (depth 5). Columnas:
  ts, ticker,
  bid_1_px, bid_1_sz, bid_2_px, bid_2_sz, ..., bid_5_px, bid_5_sz,
  ask_1_px, ask_1_sz, ask_2_px, ask_2_sz, ..., ask_5_px, ask_5_sz

Uso:
    python -m scripts.export_order_book_l2
    python -m scripts.export_order_book_l2 --out /var/backups
    python -m scripts.export_order_book_l2 --out /var/backups --ticker "MERV - XMEV - AL30 - CI"
"""
from __future__ import annotations

import argparse
import csv
import re
from datetime import UTC, datetime
from pathlib import Path

from core.mongo import get_mongo_client_read

DEPTH = 5
HEADER = (
    ["ts", "ticker"]
    + [f"bid_{i}_px" for i in range(1, DEPTH + 1)]
    + [f"bid_{i}_sz" for i in range(1, DEPTH + 1)]
    + [f"ask_{i}_px" for i in range(1, DEPTH + 1)]
    + [f"ask_{i}_sz" for i in range(1, DEPTH + 1)]
)


def _safe_filename(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", ticker)


def _flatten_book(side: list, depth: int) -> list:
    """Devuelve [px_1, ..., px_N, sz_1, ..., sz_N] para un side
    (lista de dicts con 'price' y 'size'). Padding con '' si faltan
    niveles."""
    pxs, szs = [], []
    for i in range(depth):
        if i < len(side):
            lvl = side[i] or {}
            pxs.append(lvl.get("price", ""))
            szs.append(lvl.get("size", ""))
        else:
            pxs.append("")
            szs.append("")
    return pxs + szs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="./",
                        help="Directorio destino (default: cwd).")
    parser.add_argument("--ticker", default=None,
                        help="Si se provee, exporta solo ese ticker.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit por ticker (0 = sin límite).")
    args = parser.parse_args()

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    coll = get_mongo_client_read()["Trading"]["OrderBookL2"]

    # Lista de tickers presentes.
    if args.ticker:
        tickers = [args.ticker]
    else:
        tickers = sorted(coll.distinct("ticker"))
    if not tickers:
        print("Sin tickers en Trading.OrderBookL2 — nada para exportar.")
        return 0

    print(f"Tickers a exportar: {len(tickers)}")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    total_docs = 0

    for t in tickers:
        fname = f"order_book_l2_{_safe_filename(t)}_{stamp}.csv"
        path = out_dir / fname

        cur = coll.find({"ticker": t}, sort=[("ts", 1)])
        if args.limit:
            cur = cur.limit(args.limit)

        n = 0
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(HEADER)
            for d in cur:
                ts = d.get("ts")
                ts_str = ts.isoformat() if isinstance(ts, datetime) else str(ts)
                row = [
                    ts_str,
                    d.get("ticker") or "",
                    *_flatten_book(d.get("bids") or [], DEPTH),
                    *_flatten_book(d.get("offers") or [], DEPTH),
                ]
                writer.writerow(row)
                n += 1
        print(f"  {t:<40} → {n:>10,} docs · {path}")
        total_docs += n

    print(f"\nTotal exportado: {total_docs:,} docs en {len(tickers)} archivo(s) CSV.")
    print(f"Output: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
