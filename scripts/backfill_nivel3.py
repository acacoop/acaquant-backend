"""Backfill de nivel_3 (persona humana / jurídica) en Clientes.Comitentes.

nivel_3 es DERIVADO de tipo_cliente (ver jobs.sync_comitentes.clasificar_nivel_3):
'Persona' → 'Persona Humana'; cualquier otro valor con dato → 'Persona Jurídica';
sin dato → no se clasifica (queda vacío). Las cuentas NUEVAS ya lo reciben en el
sync al crearse; este script completa las EXISTENTES (una sola vez).

Por defecto SOLO completa las que tienen nivel_3 vacío/null (no pisa lo que la
mesa haya corregido a mano). `--force` re-deriva TODAS (nunca borra a null).

Uso:
    python -m scripts.backfill_nivel3 --dry-run   # muestra el reparto, NO escribe
    python -m scripts.backfill_nivel3             # aplica (solo las vacías)
    python -m scripts.backfill_nivel3 --force     # re-deriva todas
"""
from __future__ import annotations

import argparse
from collections import Counter

from pymongo import UpdateOne

from core.mongo import get_mongo_client
from jobs.sync_comitentes import clasificar_nivel_3

DB = "Clientes"
COL = "Comitentes"

_EMPTY = ("", None)


def run(*, dry_run: bool = False, force: bool = False) -> None:
    col = get_mongo_client()[DB][COL]
    cur = col.find({}, {"_id": 0, "id_cuenta": 1, "tipo_cliente": 1, "nivel_3": 1})

    por_tipo: Counter[str] = Counter()
    por_nivel: Counter[str] = Counter()
    ops: list[UpdateOne] = []
    n_total = n_sin_id = n_sin_clasif = n_ya_ok = n_protegidas = 0

    for c in cur:
        n_total += 1
        idc = str(c.get("id_cuenta") or "").strip()
        if not idc:
            n_sin_id += 1
            continue
        nuevo = clasificar_nivel_3(c.get("tipo_cliente"))
        actual = c.get("nivel_3")
        por_tipo[str(c.get("tipo_cliente") or "(vacío)")] += 1
        por_nivel[nuevo or "(sin clasificar)"] += 1

        if nuevo is None:                       # tipo_cliente vacío → no clasifico
            n_sin_clasif += 1
            continue
        if actual not in _EMPTY and not force:  # ya cargado a mano → respeto
            n_protegidas += 1
            continue
        if actual == nuevo:                     # ya está bien → no reescribo
            n_ya_ok += 1
            continue
        ops.append(UpdateOne({"id_cuenta": idc}, {"$set": {"nivel_3": nuevo}}))

    print(f"Cuentas totales:                 {n_total}")
    print("tipo_cliente (lo que hay en la base) →")
    for k, v in por_tipo.most_common():
        print(f"   {k:<34} {v}")
    print("nivel_3 que se derivaría →")
    for k, v in por_nivel.most_common():
        print(f"   {k:<22} {v}")
    print(f"Sin clasificar (tipo_cliente vacío): {n_sin_clasif}")
    print(f"Ya correctas (no cambian):           {n_ya_ok}")
    if not force:
        print(f"Protegidas (ya tenían nivel_3):      {n_protegidas}")
    if n_sin_id:
        print(f"Saltadas sin id_cuenta:              {n_sin_id}")
    print(f"A ACTUALIZAR:                        {len(ops)}")

    if dry_run:
        print("\nDRY-RUN: no se escribió nada.")
        return
    if ops:
        res = col.bulk_write(ops, ordered=False)
        print(f"\nOK: {res.modified_count} cuentas actualizadas.")
    else:
        print("\nNada para actualizar.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="no escribe, solo muestra el reparto")
    ap.add_argument("--force", action="store_true",
                    help="re-deriva TODAS (pisa nivel_3 existente; nunca borra a null)")
    args = ap.parse_args()
    run(dry_run=args.dry_run, force=args.force)
