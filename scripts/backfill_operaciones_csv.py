"""backfill_operaciones_csv.py — carga un CSV a CashFlow.Operaciones, directo
y rápido (sin navegador). Mismo normalizador que la vista de Manager.

Mucho más rápido que el upload del browser: lee el CSV local, normaliza y
upsertea por boleto en lotes grandes contra Atlas. Idempotente (re-correr
actualiza, no duplica). Crea el índice único en el primer lote.

Uso (donde esté el CSV + acceso a Atlas; ej. el Droplet tras scp del archivo):
    venv/bin/python -m scripts.backfill_operaciones_csv --csv operaciones.historico.csv
    venv/bin/python -m scripts.backfill_operaciones_csv --csv x.csv --batch 10000
"""
from __future__ import annotations

import argparse
import csv

from api.services import operaciones_informes as svc
from core.mongo import get_mongo_client


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="path al CSV")
    ap.add_argument("--batch", type=int, default=5000, help="filas por lote (default 5000)")
    args = ap.parse_args()

    coll = get_mongo_client()["CashFlow"]["Operaciones"]
    tot = {"recibidas": 0, "upsertadas": 0, "modificadas": 0, "sin_boleto": 0}
    lote: list[dict] = []
    primero = True

    def flush() -> None:
        nonlocal lote, primero
        if not lote:
            return
        res = svc.ingestar_filas(coll, lote, crear_indice=primero)
        primero = False
        for k in tot:
            tot[k] += res.get(k, 0)
        print(f"  +{res['upsertadas']} nuevas / {res['modificadas']} act / "
              f"{res['sin_boleto']} sin boleto · acumulado: {tot['upsertadas'] + tot['modificadas']:,}")
        lote = []

    # utf-8-sig: tolera BOM. El normalizador mapea los headers (con/sin acento).
    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            lote.append(row)
            if len(lote) >= args.batch:
                flush()
    flush()

    print(f"\n✅ Listo. recibidas={tot['recibidas']:,} · "
          f"nuevas={tot['upsertadas']:,} · actualizadas={tot['modificadas']:,} · "
          f"sin_boleto={tot['sin_boleto']:,}")
    print("Después corré:  python -m scripts.enrich_operaciones")


if __name__ == "__main__":
    main()
