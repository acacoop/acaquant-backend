"""Normaliza los `ticker` en Trading.Curvas: collapse whitespace + trim.

Útil cuando un seed se cargó con espacios múltiples accidentales
(ej. 'MERV - XMEV -   GD35D - 24hs' con doble espacio → se pasa a
'MERV - XMEV - GD35D - 24hs'). Si el ticker no matchea al formato
exacto de ROFEX, el motor_rofex falla al suscribirlo via WS y todo
el libro queda desacomodado.

Uso:
    python -m scripts.fix_curva_tickers           # corrige los que están mal
    python -m scripts.fix_curva_tickers --dry     # solo lista, no toca Mongo
"""
from __future__ import annotations

import argparse
import re

from core.mongo import get_mongo_client


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true",
                        help="Lista los cambios pero no escribe en Mongo")
    args = parser.parse_args()

    client = get_mongo_client()
    col = client["Trading"]["Curvas"]

    docs = list(col.find({"ticker": {"$exists": True}}, {"ticker": 1, "ticker_corto": 1}))
    cambios = 0

    for d in docs:
        original = d.get("ticker") or ""
        # Collapse cualquier whitespace repetido a un único espacio + trim.
        limpio = re.sub(r"\s+", " ", original).strip()
        if limpio != original:
            print(f"  {d.get('ticker_corto')!r:<10}  {original!r} -> {limpio!r}")
            cambios += 1
            if not args.dry:
                col.update_one({"_id": d["_id"]}, {"$set": {"ticker": limpio}})

    if cambios == 0:
        print("OK: todos los tickers están bien formados.")
    else:
        if args.dry:
            print(f"\nDRY RUN: {cambios} tickers se corregirían. Re-correr sin --dry para aplicar.")
        else:
            print(f"\n{cambios} tickers corregidos. Reiniciar motor_rofex + motor_curvas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
