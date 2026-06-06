"""drop_espejo.py — dropea una colección espejo *API ya sin consumidores.

Genérico para la migración "matar los espejos". Correr DESPUÉS de deployar la API
nueva y confirmar que las vistas leen de la fuente. IRREVERSIBLE → dry-run por
default; dropea con --apply. Idempotente. Se borra cuando termine la migración.

    python -m scripts.drop_espejo TitulosAPI ValuacionesAPI
    python -m scripts.drop_espejo TitulosAPI ValuacionesAPI --apply
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", help="base de datos (ej. TitulosAPI)")
    ap.add_argument("coll", help="colección (ej. ValuacionesAPI)")
    ap.add_argument("--apply", action="store_true", help="dropea de verdad (sin esto: dry-run)")
    args = ap.parse_args()

    db = get_mongo_client()[args.db]
    if args.coll not in db.list_collection_names():
        print(f"{args.db}.{args.coll} no existe (ya borrada) — nada que hacer.")
        return 0
    n = db[args.coll].estimated_document_count()
    if args.apply:
        db[args.coll].drop()
        print(f"✔ {args.db}.{args.coll} DROPEADA ({n:,} docs)")
    else:
        print(f"[DRY-RUN] {args.db}.{args.coll}: {n:,} docs — se dropearía. Corré con --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
