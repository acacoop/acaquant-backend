"""rename_limite_fondeo_a_cupo.py — migración del rename del subdoc en Clientes.Comitentes.

Renombra:
    limite_fondeo               → cupo
    limite_fondeo.disponible_ars → cupo.transaccional_ars
    limite_fondeo.utilizado_ars  → cupo.usado_ars

Otros campos del subdoc (`utilizacion_pct`, `cargado_en`, `fuente`) conservan
nombre y se mueven con el rename del parent.

Idempotente: si ya está renombrado, no hace nada. `$rename` de Mongo solo
opera sobre docs que tengan el campo origen, así que correr varias veces es
seguro.

Default --dry-run: imprime cuántos docs serían afectados. Pasar --apply para
ejecutarlo.

Uso:
    python -m scripts.rename_limite_fondeo_a_cupo                 # dry-run
    python -m scripts.rename_limite_fondeo_a_cupo --apply         # aplica
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

DB = "Clientes"
COL = "Comitentes"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="ejecutar (default: dry-run)")
    args = ap.parse_args()

    col = get_mongo_client()[DB][COL]

    n_parent = col.count_documents({"limite_fondeo": {"$exists": True}})
    n_disp   = col.count_documents({"limite_fondeo.disponible_ars": {"$exists": True}})
    n_util   = col.count_documents({"limite_fondeo.utilizado_ars":  {"$exists": True}})
    print(f"docs con `limite_fondeo`:                  {n_parent}")
    print(f"docs con `limite_fondeo.disponible_ars`:   {n_disp}")
    print(f"docs con `limite_fondeo.utilizado_ars`:    {n_util}")

    n_cupo = col.count_documents({"cupo": {"$exists": True}})
    if n_cupo:
        print(f"docs con `cupo` (ya renombrado):           {n_cupo}")

    if not args.apply:
        print("\n(dry-run) — pasar --apply para ejecutar.")
        return

    print("\nAplicando migración…")

    # Paso 1: renombrar inner fields ANTES de mover el subdoc — si lo hacés
    # al revés `$rename` falla porque ya no hay path "limite_fondeo.*".
    if n_disp:
        r = col.update_many(
            {"limite_fondeo.disponible_ars": {"$exists": True}},
            {"$rename": {"limite_fondeo.disponible_ars": "limite_fondeo.transaccional_ars"}},
        )
        print(f"  disponible_ars → transaccional_ars : matched={r.matched_count} modified={r.modified_count}")
    if n_util:
        r = col.update_many(
            {"limite_fondeo.utilizado_ars": {"$exists": True}},
            {"$rename": {"limite_fondeo.utilizado_ars": "limite_fondeo.usado_ars"}},
        )
        print(f"  utilizado_ars  → usado_ars         : matched={r.matched_count} modified={r.modified_count}")

    # Paso 2: renombrar el subdoc parent.
    if n_parent:
        r = col.update_many(
            {"limite_fondeo": {"$exists": True}},
            {"$rename": {"limite_fondeo": "cupo"}},
        )
        print(f"  limite_fondeo  → cupo              : matched={r.matched_count} modified={r.modified_count}")

    # Verificación post.
    print("\nPost-migración:")
    print(f"  docs con `limite_fondeo`: {col.count_documents({'limite_fondeo': {'$exists': True}})}")
    print(f"  docs con `cupo`:          {col.count_documents({'cupo': {'$exists': True}})}")
    print("OK.")


if __name__ == "__main__":
    main()
