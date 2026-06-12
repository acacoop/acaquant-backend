"""scripts/seed_spcx_cedear.py — alta de SPCX en el maestro Trading.Cedears.

SPCX es un ETF US que NO tiene CEDEAR cotizando en BYMA, pero la mesa lo quiere
ver en la vista ADRs del Scanner (/renta-variable). El Scanner arma una fila por
cada doc `activo:True` del maestro y muestra los datos USD (PreciosAcciones EOD +
AdrSnapshot live) aunque no haya snapshot CEDEAR de BYMA — las columnas CEDEAR
quedan vacías (verificado en api/services/scanner.py::get_cedears_scanner).

Con esta alta:
  - jobs/precios_acciones_daily.py (universo = Cedears activos) pide SPCX a Yahoo → PreciosAcciones.
  - jobs/adr_live.py (idem) pide SPCX a Finnhub cada 15' → AdrSnapshot.
  - el Scanner lo muestra en la vista ADR.

Idempotente: upsert por ticker_corto. Re-correrlo no duplica.

Uso:
    python -m scripts.seed_spcx_cedear --dry-run    # muestra qué haría, NO escribe
    python -m scripts.seed_spcx_cedear              # aplica el upsert
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

# Doc del maestro. Campos que lee el Scanner: ticker (key de join al snapshot
# BYMA — acá no hay, queda de placeholder), ticker_corto, nombre, underlying,
# sector, activo. `sin_cedear` es marca explícita (ETF/ADR sin cedear en BYMA).
DOC = {
    "ticker":        "SPCX",   # no hay cedear BYMA real → placeholder = símbolo
    "ticker_corto":  "SPCX",
    "underlying":    "SPCX",   # símbolo US para PreciosAcciones / AdrSnapshot
    "nombre":        "SPCX",
    "sector":        None,
    "ratio_cedear":  None,
    "sin_cedear":    True,
    "activo":        True,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="no escribe, solo muestra")
    args = ap.parse_args()

    col = get_mongo_client()["Trading"]["Cedears"]
    existe = col.find_one({"ticker_corto": "SPCX"}, {"_id": 0, "ticker_corto": 1})

    print(f"{'[DRY-RUN] ' if args.dry_run else ''}upsert Trading.Cedears ticker_corto=SPCX")
    print(f"  ya existe: {'sí' if existe else 'no'}")
    print(f"  doc: {DOC}")

    if args.dry_run:
        print("  (dry-run: no se escribió nada)")
        return 0

    res = col.update_one({"ticker_corto": "SPCX"}, {"$set": DOC}, upsert=True)
    print(f"  matched={res.matched_count} modified={res.modified_count} "
          f"upserted={'sí' if res.upserted_id else 'no'}")
    print("OK. Próxima corrida de precios_acciones_daily / adr_live ya incluye SPCX.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
