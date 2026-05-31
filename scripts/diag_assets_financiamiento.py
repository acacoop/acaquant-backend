"""Muestra read-only de assets de CARTERA FINANCIAMIENTO en Valuaciones.Assets.

One-shot para inspeccionar el shape de estas unidades antes de definir el
backfill. Tolera el valor nuevo ('FINANCIAMIENTO') y el legacy
('CARTERA FINANCIAMIENTO').

Uso:
    python -m scripts.diag_assets_financiamiento
    python -m scripts.diag_assets_financiamiento --limit 10
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client_read

_CAMPOS = ("CARTERA", "EMISOR", "INSTRUMENTO", "CLASE_ACTIVO",
           "CALIFICACION", "TICKER", "VENCIMIENTO")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=6, help="Cuántas unidades mostrar")
    args = ap.parse_args()

    col = get_mongo_client_read()["Valuaciones"]["Assets"]
    filtro = {"CARTERA": {"$in": ["FINANCIAMIENTO", "CARTERA FINANCIAMIENTO"]}}

    total = col.count_documents(filtro)
    print("=== Valuaciones.Assets · CARTERA FINANCIAMIENTO ===")
    print(f"total docs: {total}\n")

    proj = {"_id": 0, "unidad": 1, **{c: 1 for c in _CAMPOS}}
    for d in col.find(filtro, proj).limit(args.limit):
        print(f"unidad: {d.get('unidad')!r}")
        for c in _CAMPOS:
            print(f"    {c:<13} {d.get(c)!r}")
        print()


if __name__ == "__main__":
    main()
