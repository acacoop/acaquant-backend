"""Seedea los 8 Bonares hard-dollar (D-only) en Trading.Curvas.

Reusa parse_csv / build_doc de seed_soberano_csv. Mismo shape que los
GDs, pero con tipo='bonares' para distinguir dentro de la curva
'soberanos'.

Fecha de emisión: placeholder 2020-09-04 (canje Guzmán). El motor no
usa este campo para calcular YTM/duration, queda como metadata.

Uso:
    python -m scripts.seed_bonares_batch          # seedea los 8
    python -m scripts.seed_bonares_batch --dry    # preview sin escribir
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.mongo import get_mongo_client
from scripts.seed_soberano_csv import build_doc, parse_csv

# (csv_filename, ticker_corto, ticker_full, fecha_emision_placeholder)
BONARES = [
    ("AE38.csv", "AE38D", "MERV - XMEV - AE38D - 24hs", "2020-09-04"),
    ("AL29.csv", "AL29D", "MERV - XMEV - AL29D - 24hs", "2020-09-04"),
    ("AL30.csv", "AL30D", "MERV - XMEV - AL30D - 24hs", "2020-09-04"),
    ("AL35.csv", "AL35D", "MERV - XMEV - AL35D - 24hs", "2020-09-04"),
    ("AL41.csv", "AL41D", "MERV - XMEV - AL41D - 24hs", "2020-09-04"),
    ("AN29.csv", "AN29D", "MERV - XMEV - AN29D - 24hs", "2020-09-04"),
    ("AO27.csv", "AO27D", "MERV - XMEV - AO27D - 24hs", "2020-09-04"),
    ("AO28.csv", "AO28D", "MERV - XMEV - AO28D - 24hs", "2020-09-04"),
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true")
    args = p.parse_args()

    base = Path("docs/soberanos")
    if not base.exists():
        raise SystemExit(f"Carpeta no existe: {base}")

    docs = []
    for fname, ticker_corto, ticker_full, fecha_emision in BONARES:
        path = base / fname
        if not path.exists():
            print(f"  ⚠ FALTA: {path} → skip")
            continue
        flujos = parse_csv(path)
        doc = build_doc(
            ticker=ticker_full,
            ticker_corto=ticker_corto,
            fecha_emision=fecha_emision,
            flujos=flujos,
            tipo="bonares",
            curva="soberanos",
        )
        docs.append(doc)
        print(f"  ✓ {ticker_corto}: {len(flujos)} flujos · vto {doc['fecha_vencimiento']}")

    if not docs:
        print("Nada para seedear.")
        return 1

    print(f"\nTotal: {len(docs)} bonares listos.")

    if args.dry:
        print("\n--dry: no se escribe en Mongo.")
        return 0

    client = get_mongo_client()
    col = client["Trading"]["Curvas"]
    nuevos = 0
    actualizados = 0
    for doc in docs:
        res = col.update_one(
            {"ticker_corto": doc["ticker_corto"]},
            {"$set": doc},
            upsert=True,
        )
        if res.upserted_id:
            nuevos += 1
        else:
            actualizados += 1
    print(f"\nOK: {nuevos} nuevos · {actualizados} actualizados")
    print("Reiniciá motor_rofex + motor_curvas para que tomen los tickers nuevos:")
    print("  systemctl restart motor_rofex motor_curvas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
