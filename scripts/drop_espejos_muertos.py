"""drop_espejos_muertos.py — dropea las colecciones espejo *API ya sin consumidores.

Se corre UNA vez, después de confirmar en prod que las vistas leen de la fuente.
Las colecciones acá ya no las lee NINGÚN código (se repuntearon a CashFlow.*):
  - CuentasAPI.AccionistasAPI, CuentasAPI.ContrapartesAPI
  - OperacionesAPI.MesaAPI, OperacionesAPI.FlujosAPI
Al quedar las DBs CuentasAPI / OperacionesAPI sin colecciones, Mongo las elimina.

IRREVERSIBLE → default --dry-run (solo muestra). Dropea solo con --apply.
Idempotente: si una colección ya no existe, la saltea.

Uso:
    python -m scripts.drop_espejos_muertos            # dry-run (no toca nada)
    python -m scripts.drop_espejos_muertos --apply    # dropea de verdad
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

# (db, colección) a dropear. NADIE las lee ya (verificado por grep + prod).
_OBJETIVOS = [
    ("CuentasAPI", "AccionistasAPI"),
    ("CuentasAPI", "ContrapartesAPI"),
    ("OperacionesAPI", "MesaAPI"),
    ("OperacionesAPI", "FlujosAPI"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="dropea de verdad (sin esto: dry-run)")
    args = ap.parse_args()

    cli = get_mongo_client()
    print(f"{'APLICANDO' if args.apply else 'DRY-RUN'} — drop de espejos muertos\n")
    for dbn, coll in _OBJETIVOS:
        db = cli[dbn]
        existe = coll in db.list_collection_names()
        if not existe:
            print(f"  - {dbn}.{coll}: no existe (ya borrada) — skip")
            continue
        n = db[coll].estimated_document_count()
        if args.apply:
            db[coll].drop()
            print(f"  ✔ {dbn}.{coll}: DROPEADA ({n:,} docs)")
        else:
            print(f"  - {dbn}.{coll}: {n:,} docs — se dropearía")

    if not args.apply:
        print("\n(dry-run: no se tocó nada. Corré con --apply para dropear.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
