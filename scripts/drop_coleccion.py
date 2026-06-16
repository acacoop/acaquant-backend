"""scripts/drop_coleccion.py — dropea UNA colección Mongo (decommission Mongo→SQL).

Herramienta RECURRENTE del decommission: dropea de a una, con guardas. Dry-run por
default (solo muestra el conteo); borra de verdad con --apply.

ANTES de --apply, confirmá la checklist del decommission:
  1. La LECTURA de esa colección ya va a SQL (flag *_SQL=1, validado).
  2. La ESCRITURA ya es SQL-native (ningún job/motor escribe esa colección en Mongo).
  3. NO hay sync_* en sync_postgres que la espeje (si lo había, ya se removió).
Si alguno no se cumple, NO la dropees (un job la recrea o un read queda sin fuente).

    python -m scripts.drop_coleccion --coleccion News.Headlines           # dry-run
    python -m scripts.drop_coleccion --coleccion News.Headlines --apply   # borra
"""
import argparse

from core.mongo import get_mongo_client


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coleccion", required=True, help="Formato DB.Coleccion (ej. News.Headlines)")
    ap.add_argument("--apply", action="store_true", help="Borra de verdad (sin esto = dry-run)")
    args = ap.parse_args()

    if "." not in args.coleccion:
        print("formato inválido — usá DB.Coleccion (ej. News.Headlines)")
        return 2
    db, coll = args.coleccion.split(".", 1)

    cli = get_mongo_client()
    if coll not in cli[db].list_collection_names():
        print(f"{db}.{coll}: no existe (¿ya dropeada?)")
        return 0
    n = cli[db][coll].estimated_document_count()

    if args.apply:
        cli[db].drop_collection(coll)
        print(f"✅ {db}.{coll} DROPEADA ({n:,} docs).")
    else:
        print(f"DRY-RUN — {db}.{coll}: {n:,} docs.\n"
              f"Confirmá la checklist (read SQL / write SQL-native / sin sync) y re-corré con --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
