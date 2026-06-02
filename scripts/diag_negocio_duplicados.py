"""diag_negocio_duplicados.py — boletos duplicados en CashFlow.NegocioMovimientos.

Read-only. NO asume nada. NegocioMovimientos tiene índice único (fecha,
comprobante), así que el MISMO comprobante en la MISMA fecha no puede repetirse.
Este diag busca lo que ese índice NO cubre:

  A. Mismo `comprobante` (string exacto) en MÁS de un documento → se repite en
     fechas distintas.
  B. Mismo NÚMERO de boleto (normalizado, sin prefijo BOL/CL) en más de un
     documento → mismo boleto escrito distinto.

Imprime conteos + ejemplos con los documentos completos.

Uso (desde la raíz del repo en el Droplet):
    venv/bin/python -m scripts.diag_negocio_duplicados
    venv/bin/python -m scripts.diag_negocio_duplicados --ejemplos 5
"""
from __future__ import annotations

import argparse
import re
from collections import defaultdict

from core.mongo import get_mongo_client_read

_DIGITS = re.compile(r"\d+")


def _norm(v) -> str | None:
    if v is None:
        return None
    grupos = _DIGITS.findall(str(v))
    return "".join(grupos) if grupos else None


def _print_doc(d: dict) -> None:
    for k in sorted(d):
        print(f"      {k}: {d[k]!r}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ejemplos", type=int, default=3)
    args = ap.parse_args()

    c = get_mongo_client_read()
    col = c["CashFlow"]["NegocioMovimientos"]

    total = col.count_documents({})
    print(f"CashFlow.NegocioMovimientos: {total} documentos\n")

    # ── A. Mismo comprobante exacto en >1 doc ───────────────────────────────
    dup_exacto = list(col.aggregate([
        {"$match": {"comprobante": {"$ne": None}}},
        {"$group": {"_id": "$comprobante", "n": {"$sum": 1}, "fechas": {"$addToSet": "$fecha"}}},
        {"$match": {"n": {"$gt": 1}}},
        {"$sort": {"n": -1}},
    ]))
    extra_exacto = sum(d["n"] - 1 for d in dup_exacto)
    print("=" * 80)
    print("A. MISMO comprobante (string exacto) en más de un documento")
    print("=" * 80)
    print(f"   Comprobantes repetidos: {len(dup_exacto)}")
    print(f"   Documentos de más:      {extra_exacto}")
    if dup_exacto:
        print(f"   Máximo de copias:       {dup_exacto[0]['n']}  (comprobante {dup_exacto[0]['_id']!r})")
        for d in dup_exacto[:5]:
            print(f"     {d['_id']!r}: {d['n']} copias · fechas={sorted(d['fechas'])}")

    # ── B. Mismo número normalizado en >1 doc ───────────────────────────────
    # Trae solo (comprobante) — liviano — y agrupa en Python por número.
    por_num: dict[str, set[str]] = defaultdict(set)   # num → set de comprobantes crudos
    num_count: dict[str, int] = defaultdict(int)
    for d in col.find({"comprobante": {"$ne": None}}, {"_id": 0, "comprobante": 1}):
        num = _norm(d.get("comprobante"))
        if num:
            num_count[num] += 1
            por_num[num].add(str(d.get("comprobante")))

    dup_num = {n: c for n, c in num_count.items() if c > 1}
    # de esos, cuáles tienen MÁS DE UN string crudo distinto (mismo nro, escrito distinto)
    dup_num_multi = {n: por_num[n] for n in dup_num if len(por_num[n]) > 1}
    print("\n" + "=" * 80)
    print("B. MISMO número (normalizado, sin prefijo) en más de un documento")
    print("=" * 80)
    print(f"   Números repetidos (cualquier causa): {len(dup_num)}")
    print(f"   ...de esos, con comprobante ESCRITO DISTINTO (BOL vs CL, etc.): {len(dup_num_multi)}")
    if dup_num_multi:
        print("   Ejemplos (número → comprobantes crudos distintos):")
        for n, crudos in list(dup_num_multi.items())[:10]:
            print(f"     {n}: {sorted(crudos)}")

    # ── Ejemplos completos del caso A ───────────────────────────────────────
    if dup_exacto:
        print("\n" + "=" * 80)
        print(f"EJEMPLOS COMPLETOS (caso A) — {args.ejemplos} comprobantes más repetidos")
        print("=" * 80)
        for d in dup_exacto[:args.ejemplos]:
            docs = list(col.find({"comprobante": d["_id"]}))
            print(f"\nComprobante {d['_id']!r} — {len(docs)} copias:")
            for i, doc in enumerate(docs, 1):
                print(f"  ── copia {i} ──")
                _print_doc(doc)

    if not dup_exacto and not dup_num_multi:
        print("\n✅ No hay boletos duplicados en NegocioMovimientos.")


if __name__ == "__main__":
    main()
