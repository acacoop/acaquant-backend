"""Backfill del campo `commodity` (SOJA/TRIGO/MAIZ/None) en CashFlow.Operaciones.

`commodity` se materializa en la ingesta (operaciones_informes.clasificar_commodity)
para que /ops/agro matchee por índice parcial en vez de escanear con regex. Este
script lo setea en los docs YA existentes y crea el índice `commodity_concertacion`.

Lo hace SERVER-SIDE con un solo `update_many` + pipeline (Mongo clasifica sin
traer docs al cliente) → robusto: no usa cursor (la versión anterior se moría con
CursorNotFound al iterar 481k docs mientras escribía). Idempotente: re-correrlo no
cambia nada si ya está aplicado. La lógica del pipeline replica EXACTAMENTE a
operaciones_informes.clasificar_commodity.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.backfill_commodity_operaciones --dry-run
    python -m scripts.backfill_commodity_operaciones

ORDEN de deploy: git pull → este backfill (a fondo) → restart api.service
(el endpoint /ops/agro nuevo necesita el campo; ingestas nuevas ya lo setean).
"""
from __future__ import annotations

import argparse

from api.services.operaciones_informes import ensure_indexes
from core.mongo import get_mongo_client

# Candidatos: solo boletos de futuros (los demás nunca son agro → commodity
# ausente, que el índice parcial ignora). Reduce la escritura a ~1/3.
_FILTRO = {"tipo_operacion": {"$regex": "Futuros", "$options": "i"}}

# Filtro del set agro completo (para el dry-run: cuántos clasifican como agro).
_AGRO = {"$and": [
    {"tipo_operacion": {"$regex": "Futuros", "$options": "i"}},
    {"tipo_operacion": {"$not": {"$regex": "Financieros", "$options": "i"}}},
    {"denominacion": {"$not": {"$regex": "OTC", "$options": "i"}}},
    {"instrumento": {"$not": {"$regex": "OTC", "$options": "i"}}},
    {"instrumento": {"$regex": "SOJ|TRI|MAI", "$options": "i"}},
]}

# Pipeline $set que replica clasificar_commodity, evaluado server-side por doc.
_PIPELINE = [{"$set": {"commodity": {"$let": {
    "vars": {
        "t": {"$toUpper": {"$ifNull": ["$tipo_operacion", ""]}},
        "i": {"$toUpper": {"$ifNull": ["$instrumento", ""]}},
        "d": {"$toUpper": {"$ifNull": ["$denominacion", ""]}},
    },
    "in": {"$cond": [
        {"$and": [
            {"$gte": [{"$indexOfCP": ["$$t", "FUTUROS"]}, 0]},
            {"$lt": [{"$indexOfCP": ["$$t", "FINANCIEROS"]}, 0]},
            {"$lt": [{"$indexOfCP": ["$$i", "OTC"]}, 0]},
            {"$lt": [{"$indexOfCP": ["$$d", "OTC"]}, 0]},
        ]},
        {"$switch": {"branches": [
            {"case": {"$gte": [{"$indexOfCP": ["$$i", "SOJ"]}, 0]}, "then": "SOJA"},
            {"case": {"$gte": [{"$indexOfCP": ["$$i", "TRI"]}, 0]}, "then": "TRIGO"},
            {"case": {"$gte": [{"$indexOfCP": ["$$i", "MAI"]}, 0]}, "then": "MAIZ"},
        ], "default": None}},
        None,
    ]},
}}}}]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="reporta sin escribir")
    args = ap.parse_args()

    coll = get_mongo_client()["CashFlow"]["Operaciones"]
    n_fut = coll.count_documents(_FILTRO)
    n_agro = coll.count_documents(_AGRO)
    print(f"Futuros (candidatos a escribir): {n_fut} | de esos, agro (SOJA/TRIGO/MAIZ): {n_agro}")

    if args.dry_run:
        ya = coll.count_documents({"commodity": {"$in": ["SOJA", "TRIGO", "MAIZ"]}})
        print(f"Ya marcados como agro hoy: {ya}")
        print("(dry-run: no se escribió ni se creó el índice)")
        return

    print("Aplicando update_many server-side (sin cursor)…")
    res = coll.update_many(_FILTRO, _PIPELINE)
    print(f"  matched={res.matched_count} modified={res.modified_count}")
    marcados = coll.count_documents({"commodity": {"$in": ["SOJA", "TRIGO", "MAIZ"]}})
    print(f"  docs con commodity agro ahora: {marcados} (esperado ≈ {n_agro})")

    print("Creando índices (incluye commodity_concertacion)…")
    ensure_indexes(coll)
    print("✅ Backfill + índices OK.")


if __name__ == "__main__":
    main()
