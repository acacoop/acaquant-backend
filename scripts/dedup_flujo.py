"""dedup_flujo.py — LEY #1: un boleto = un documento en CashFlow.Flujo.

One-shot. Hace dos cosas, en orden:
  1. Deduplica: para cada boleto con más de un documento, deja UNO (el de menor
     _id, determinístico) y borra el resto.
  2. Crea el índice ÚNICO PARCIAL sobre `boleto` → a partir de acá Mongo RECHAZA
     físicamente cualquier intento de insertar un boleto repetido.

El índice es PARCIAL (solo aplica a docs cuyo `boleto` es string/int): los docs
sin boleto (None) quedan permitidos y no se tocan — no se pueden deduplicar por
boleto porque no tienen.

Idempotente: si ya está limpio, no borra nada; si el índice ya existe, no falla.

Uso (desde la raíz del repo en el Droplet):
    venv/bin/python -m scripts.dedup_flujo            # ejecuta (borra + indexa)
    venv/bin/python -m scripts.dedup_flujo --dry      # solo reporta, no toca nada
"""
from __future__ import annotations

import argparse

from pymongo.errors import DuplicateKeyError

from core.mongo import get_mongo_client

# Tipos de boleto "reales" (excluye None / sin boleto).
_BOLETO_TIPOS = ["string", "int", "long", "double"]
_INDEX_NAME = "uq_boleto"
_PARTIAL_FILTER = {"boleto": {"$type": _BOLETO_TIPOS}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="no borra ni crea índice, solo reporta")
    args = ap.parse_args()

    client = get_mongo_client()
    col = client["CashFlow"]["Flujo"]

    total = col.count_documents({})
    print(f"CashFlow.Flujo: {total} documentos\n")

    # ── 1. Encontrar duplicados (boleto real con >1 doc) ────────────────────
    dups = list(col.aggregate([
        {"$match": _PARTIAL_FILTER},
        {"$group": {"_id": "$boleto", "ids": {"$push": "$_id"}, "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
        {"$sort": {"n": -1}},
    ]))
    a_borrar = []
    for d in dups:
        ids = sorted(d["ids"], key=lambda x: (str(type(x)), str(x)))
        a_borrar.extend(ids[1:])  # deja el primero, borra el resto

    print(f"Boletos duplicados: {len(dups)}")
    print(f"Documentos a borrar (copias sobrantes): {len(a_borrar)}")
    if dups[:5]:
        print("Ejemplos:")
        for d in dups[:5]:
            print(f"    boleto {d['_id']!r}: {d['n']} copias → deja 1, borra {d['n'] - 1}")

    if args.dry:
        print("\n[DRY] No se borró nada ni se creó el índice.")
        print("Correr sin --dry para aplicar.")
        return

    # ── 2. Borrar las copias sobrantes ──────────────────────────────────────
    if a_borrar:
        res = col.delete_many({"_id": {"$in": a_borrar}})
        print(f"\n🗑️  {res.deleted_count} documentos duplicados borrados.")
    else:
        print("\nNada que borrar.")

    # ── 3. Crear el índice único parcial ────────────────────────────────────
    try:
        col.create_index(
            [("boleto", 1)],
            name=_INDEX_NAME,
            unique=True,
            partialFilterExpression=_PARTIAL_FILTER,
        )
        print(f"✅ Índice único '{_INDEX_NAME}' creado sobre `boleto` (parcial: solo boletos reales).")
    except DuplicateKeyError as e:
        print(f"\n⚠ No se pudo crear el índice — todavía hay duplicados: {e}")
        print("  Re-correr el script (la dedup de arriba debería haberlos sacado).")
        return

    restante = col.count_documents({})
    print(f"\nCashFlow.Flujo: {restante} documentos (antes {total}). LEY aplicada.")


if __name__ == "__main__":
    main()
