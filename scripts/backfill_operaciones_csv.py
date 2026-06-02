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


def cargar_fresh(coll, csv_path: str) -> None:
    """Carga RÁPIDA: dropea, deduplica en memoria, insert_many sin índice, e
    índice al final. Mucho más veloz que el upsert (no hay lookup por doc ni
    mantenimiento de índice durante la carga). Para la carga histórica inicial."""
    from datetime import UTC, datetime

    ahora = datetime.now(UTC)
    docs: dict[str, dict] = {}   # boleto → doc (dedup en memoria, última gana)
    leidas = 0
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            leidas += 1
            d = svc.normalizar_fila(row)
            if d:
                d["ingestado_en"] = ahora
                docs[d["boleto"]] = d
    unicos = list(docs.values())
    print(f"Leídas {leidas:,} filas → {len(unicos):,} boletos únicos. Cargando…")

    coll.drop()  # arranca limpio (borra cargas parciales previas)
    CHUNK = 25000
    for i in range(0, len(unicos), CHUNK):
        coll.insert_many(unicos[i:i + CHUNK], ordered=False)
        print(f"  insertados {min(i + CHUNK, len(unicos)):,}/{len(unicos):,}")
    svc.ensure_indexes(coll)  # índice único al final (rápido sobre data ya limpia)
    print(f"\n✅ FRESH listo: {len(unicos):,} operaciones · {leidas - len(unicos):,} duplicados descartados.")
    print("Después corré:  python -m scripts.enrich_operaciones")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="path al CSV")
    ap.add_argument("--batch", type=int, default=5000, help="filas por lote (default 5000)")
    ap.add_argument("--fresh", action="store_true",
                    help="carga rápida: dropea + insert_many + índice al final (carga inicial)")
    args = ap.parse_args()

    coll = get_mongo_client()["CashFlow"]["Operaciones"]

    if args.fresh:
        cargar_fresh(coll, args.csv)
        return

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
