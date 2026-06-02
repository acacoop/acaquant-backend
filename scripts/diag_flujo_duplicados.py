"""diag_flujo_duplicados.py — boletos duplicados en CashFlow.Flujo.

Read-only. NO asume nada: cuenta los boletos con más de un documento e imprime
los documentos COMPLETOS (todos los campos, incluido _id) de unos ejemplos, para
ver con los ojos si las copias son idénticas o difieren en algún campo.

Uso (desde la raíz del repo en el Droplet):
    venv/bin/python -m scripts.diag_flujo_duplicados
    venv/bin/python -m scripts.diag_flujo_duplicados --ejemplos 5
    venv/bin/python -m scripts.diag_flujo_duplicados --boleto "BOL 2026071504"
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client_read


def _print_doc(d: dict) -> None:
    for k in sorted(d):
        print(f"      {k}: {d[k]!r}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ejemplos", type=int, default=3,
                    help="cuántos boletos duplicados mostrar enteros (default 3)")
    ap.add_argument("--boleto", default=None,
                    help="mostrar los documentos de ESTE boleto puntual")
    args = ap.parse_args()

    c = get_mongo_client_read()
    flujo = c["CashFlow"]["Flujo"]

    total = flujo.count_documents({})
    print(f"CashFlow.Flujo: {total} documentos en total\n")

    # ── Caso puntual ────────────────────────────────────────────────────────
    if args.boleto:
        docs = list(flujo.find({"boleto": args.boleto}))
        print(f"Boleto {args.boleto!r}: {len(docs)} documento(s)")
        for i, d in enumerate(docs, 1):
            print(f"  ── copia {i} ──")
            _print_doc(d)
        return

    # ── Conteo de duplicados ────────────────────────────────────────────────
    dup = list(flujo.aggregate([
        {"$group": {"_id": "$boleto", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
        {"$sort": {"n": -1}},
    ]))
    extra = sum(r["n"] - 1 for r in dup)
    print(f"Boletos con MÁS de un documento: {len(dup)}")
    print(f"Documentos 'de más' (duplicados que sobran): {extra}")
    if dup:
        print(f"Máximo de copias de un mismo boleto: {dup[0]['n']}  (boleto {dup[0]['_id']!r})")

    if not dup:
        print("\nNo hay boletos duplicados.")
        return

    # ── Ejemplos enteros ────────────────────────────────────────────────────
    print(f"\n{'=' * 80}")
    print(f"EJEMPLOS — documentos completos de los {args.ejemplos} boletos más duplicados")
    print("=" * 80)
    for r in dup[:args.ejemplos]:
        boleto = r["_id"]
        docs = list(flujo.find({"boleto": boleto}))
        print(f"\nBoleto {boleto!r} — {len(docs)} copias:")
        for i, d in enumerate(docs, 1):
            print(f"  ── copia {i} ──")
            _print_doc(d)


if __name__ == "__main__":
    main()
