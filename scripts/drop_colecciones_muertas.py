"""scripts/drop_colecciones_muertas.py — dropea colecciones Mongo confirmadas MUERTAS.

Paso 1 de la migración Mongo→SQL (decommission). Estas 4 colecciones no las escribe ni
lee ningún código vivo (verificado por grep); son restos de features eliminadas (AuM,
asistente IA). Las refs en crear_indices/db_maintenance se limpiaron en el mismo cambio
para que no las recreen.

Default DRY-RUN (solo muestra conteos). Borra de verdad solo con --apply.

    python -m scripts.drop_colecciones_muertas            # dry-run (no borra)
    python -m scripts.drop_colecciones_muertas --apply    # borra
"""
import sys

from core.mongo import get_mongo_client

# (db, colección) — confirmadas muertas, sin reader/writer en el código vivo.
_MUERTAS = [
    ("Manager",     "AsistenteLogs"),    # asistente IA eliminado del repo
    ("Manager",     "AumBackfillLog"),   # AuM eliminada 2026-06-15
    ("Valuaciones", "AuMResumen"),       # AuM eliminada (estaba vacía)
    ("Valuaciones", "AumBackfillRuns"),  # AuM eliminada (estaba vacía)
]


def main() -> int:
    apply = "--apply" in sys.argv
    cli = get_mongo_client()
    print(f"{'APPLY (BORRA)' if apply else 'DRY-RUN (no borra nada)'} — {len(_MUERTAS)} colecciones\n")
    for db, coll in _MUERTAS:
        existe = coll in cli[db].list_collection_names()
        if not existe:
            print(f"  {db}.{coll:<24} (no existe — ya borrada)")
            continue
        n = cli[db][coll].estimated_document_count()
        if apply:
            cli[db].drop_collection(coll)
            print(f"  {db}.{coll:<24} {n:>10,} docs → DROPEADA ✅")
        else:
            print(f"  {db}.{coll:<24} {n:>10,} docs → se borraría")
    if not apply:
        print("\n(dry-run) Re-correr con --apply para borrar de verdad.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
