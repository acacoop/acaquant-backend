"""Borra el `cupo` (subdoc completo) de UNA cuenta — para corregir una carga
manual mala. La deja como "sin cupo cargado" (la segmentación la trata como tal).

Dry-run por default: muestra el cupo actual sin tocar nada. `--apply` lo borra.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.clear_cupo_cuenta --id 805            # dry-run (muestra)
    python -m scripts.clear_cupo_cuenta --id 805 --apply    # borra el cupo
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", required=True, help="id_cuenta a limpiar")
    ap.add_argument("--apply", action="store_true", help="ejecutar (default: dry-run)")
    args = ap.parse_args()

    col = get_mongo_client()["Clientes"]["Comitentes"]
    idc = str(args.id).strip()
    doc = col.find_one({"id_cuenta": idc}, {"_id": 0, "id_cuenta": 1, "denominacion": 1, "nivel_3": 1, "cupo": 1})
    if not doc:
        print(f"No existe id_cuenta={idc!r}.")
        return
    print(f"[{doc.get('id_cuenta')}] {doc.get('denominacion')}  | nivel_3={doc.get('nivel_3')}")
    print(f"  cupo actual: {doc.get('cupo')}")

    if not args.apply:
        print("\n(dry-run) — pasar --apply para BORRAR el cupo de esta cuenta.")
        return

    res = col.update_one({"id_cuenta": idc}, {"$unset": {"cupo": ""}})
    print(f"\nOK. Cupo borrado. matched={res.matched_count} modified={res.modified_count}")


if __name__ == "__main__":
    main()
