"""Backfill del campo `es_cierre` (bool) en CashFlow.Operaciones.

`es_cierre` marca los cierres de caución (tipo_operacion contiene 'Cierre'), que
NO entran a las sumatorias de /ops/* (la apertura ya cuenta el volumen). Se
materializa en la ingesta (operaciones_informes._aplicar_enrich); este script lo
setea en los docs YA existentes para que el filtro `es_cierre: False` de
`_ops_match` no deje afuera a los boletos viejos.

Server-side (update_many + pipeline, sin cursor). Idempotente. Crea el índice
`moneda_escierre_concertacion` y dropea el viejo `moneda_concertacion` (redundante).

ORDEN de deploy (IMPORTANTE):
    git pull
    python -m scripts.backfill_es_cierre_operaciones --dry-run   # mirás
    python -m scripts.backfill_es_cierre_operaciones             # aplica
    systemctl restart api.service   # RECIÉN ACÁ el código nuevo usa es_cierre: False
Si reiniciás la API ANTES del backfill, /ops/* dejaría afuera los docs viejos
(sin el campo) hasta que corra el backfill.

Uso:
    python -m scripts.backfill_es_cierre_operaciones --dry-run
    python -m scripts.backfill_es_cierre_operaciones
"""
from __future__ import annotations

import argparse

from pymongo.errors import OperationFailure

from api.services.operaciones_informes import ensure_indexes
from core.mongo import get_mongo_client

# Pipeline $set que replica _aplicar_enrich: es_cierre = 'CIERRE' in upper(tipo_op).
_PIPELINE = [{"$set": {"es_cierre": {"$regexMatch": {
    "input": {"$toUpper": {"$ifNull": ["$tipo_operacion", ""]}},
    "regex": "CIERRE",
}}}}]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="reporta sin escribir")
    args = ap.parse_args()

    coll = get_mongo_client()["CashFlow"]["Operaciones"]
    total = coll.count_documents({})
    cierres = coll.count_documents({"tipo_operacion": {"$regex": "Cierre", "$options": "i"}})
    ya = coll.count_documents({"es_cierre": {"$exists": True}})
    print(f"Operaciones: {total:,} docs | cierres (regex): {cierres:,} | "
          f"ya tienen es_cierre: {ya:,}")

    if args.dry_run:
        print(f"Al aplicar: TODOS los docs quedan con es_cierre (False en {total - cierres:,}, "
              f"True en {cierres:,}).")
        print("(dry-run: no se escribió ni se tocaron índices)")
        return

    print("Aplicando update_many server-side (sin cursor)…")
    res = coll.update_many({}, _PIPELINE)
    print(f"  matched={res.matched_count} modified={res.modified_count}")
    con_campo = coll.count_documents({"es_cierre": {"$exists": True}})
    print(f"  docs con es_cierre ahora: {con_campo:,} (esperado {total:,})")

    print("Creando índices (incluye moneda_escierre_concertacion)…")
    ensure_indexes(coll)
    # El viejo moneda_concertacion queda redundante (lo cubre el nuevo) → lo dropeamos.
    try:
        coll.drop_index("moneda_concertacion")
        print("  índice viejo moneda_concertacion: dropeado")
    except OperationFailure:
        print("  índice viejo moneda_concertacion: no existía (ok)")
    print("✅ Backfill + índices OK.")


if __name__ == "__main__":
    main()
