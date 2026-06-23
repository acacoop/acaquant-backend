"""drop_timesales_mongo.py — dropea Trading.TimeSales de Mongo. IRREVERSIBLE.

TimeSales pasó a SQL-only (mercado.timesales): el motor valores.py ya NO la escribe
(decommission 2026-06-22), todos los lectores leen SQL (tape, MCP, breakevens vía
MarketSnapshot). Esta colección de ~5.6M docs ya no la usa nada.

⚠️ ORDEN OBLIGATORIO antes de correr esto:
  1. Deployar el código SQL-only (git pull + el motor valores reiniciado/parado).
  2. Tener RENTA_FIJA_SQL=1 (el tape lee SQL) — si no, el tape quedaría sin fuente.
  Si el motor viejo (que escribía Mongo) sigue corriendo, la colección se RECREA.

Dry-run por default.
    python -m scripts.drop_timesales_mongo            # cuenta
    python -m scripts.drop_timesales_mongo --apply    # DROPEA
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="ejecutar el DROP (default: dry-run)")
    args = ap.parse_args()

    trd = get_mongo_client()["Trading"]
    n = trd["TimeSales"].estimated_document_count()
    print(f"Mongo Trading.TimeSales: ~{n:,} docs")

    if not args.apply:
        print("\n(DRY-RUN — nada borrado. Correr con --apply.)")
        print("OJO: el motor valores tiene que estar en SQL-only (deployado) o la recrea.")
        return 0

    trd["TimeSales"].drop()
    print("\n✅ Trading.TimeSales DROPEADA. TimeSales vive solo en SQL (mercado.timesales).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
