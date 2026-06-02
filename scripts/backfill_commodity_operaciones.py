"""Backfill del campo `commodity` (SOJA/TRIGO/MAIZ/None) en CashFlow.Operaciones.

`commodity` ahora se materializa en la ingesta (operaciones_informes.
clasificar_commodity) para que /ops/agro matchee por índice parcial en vez de
escanear la colección con regex. Este script lo setea en los docs YA existentes
y crea el índice `commodity_concertacion`.

Idempotente: sólo escribe los docs cuyo `commodity` calculado difiere del
guardado. Corré con --dry-run primero para ver el conteo sin tocar nada.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.backfill_commodity_operaciones --dry-run
    python -m scripts.backfill_commodity_operaciones

ORDEN de deploy recomendado: git pull → este backfill → restart api.service
(el endpoint nuevo necesita el campo materializado; ingestas nuevas ya lo setean).
"""
from __future__ import annotations

import argparse
from collections import Counter

from pymongo import UpdateOne

from api.services.operaciones_informes import clasificar_commodity, ensure_indexes
from core.mongo import get_mongo_client

_PROJ = {"_id": 0, "boleto": 1, "tipo_operacion": 1, "denominacion": 1,
         "instrumento": 1, "commodity": 1}
_BATCH = 1000


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="reporta sin escribir")
    args = ap.parse_args()

    coll = get_mongo_client()["CashFlow"]["Operaciones"]
    total = coll.estimated_document_count()
    print(f"Operaciones: ~{total} docs | dry_run={args.dry_run}")

    dist: Counter[str] = Counter()      # distribución final de commodity
    cambios: Counter[str] = Counter()   # de qué→a qué cambia (sólo los que cambian)
    pend: list[UpdateOne] = []
    n_cambios = 0
    n_sin_boleto = 0

    for d in coll.find({}, _PROJ):
        boleto = d.get("boleto")
        actual = d.get("commodity", "__ausente__")
        nuevo = clasificar_commodity(
            d.get("tipo_operacion"), d.get("denominacion"), d.get("instrumento"),
        )
        dist[nuevo or "None"] += 1
        if actual == nuevo:
            continue
        if boleto is None:
            n_sin_boleto += 1
            continue
        n_cambios += 1
        cambios[f"{actual if actual != '__ausente__' else '(ausente)'} → {nuevo or 'None'}"] += 1
        if not args.dry_run:
            pend.append(UpdateOne({"boleto": boleto}, {"$set": {"commodity": nuevo}}))
            if len(pend) >= _BATCH:
                coll.bulk_write(pend, ordered=False)
                pend.clear()

    if pend and not args.dry_run:
        coll.bulk_write(pend, ordered=False)

    print("\nDistribución de commodity (calculada):")
    for k in ("SOJA", "TRIGO", "MAIZ", "None"):
        if dist.get(k):
            print(f"  {k:<6} {dist[k]}")
    print(f"\nDocs que {'cambiarían' if args.dry_run else 'cambiaron'}: {n_cambios}")
    for k, v in cambios.most_common():
        print(f"  {k:<24} {v}")
    if n_sin_boleto:
        print(f"Docs sin boleto (no actualizables): {n_sin_boleto}")

    if not args.dry_run:
        print("\nCreando índices (incluye commodity_concertacion)…")
        ensure_indexes(coll)
        print("✅ Backfill + índices OK.")
    else:
        print("\n(dry-run: no se escribió ni se creó el índice)")


if __name__ == "__main__":
    main()
