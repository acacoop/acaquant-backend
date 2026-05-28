"""rename_nivel_valor.py — renombra un valor puntual de nivel_1..5 en Comitentes.

Para corregir tipeos / mal cargados como `INSTITUCIONAL` → `INSTITUCIONALES`
sin tocar el resto del dataset. Match exacto (case-sensitive — los segmentos
viven en MAYÚSCULAS por convención, ver
`scripts/backfill_segmentos_upper.py`).

Uso:
    # dry-run (default): cuenta cuántos docs cambian, no escribe
    python -m scripts.rename_nivel_valor --campo nivel_1 --de INSTITUCIONAL --a INSTITUCIONALES

    # aplica
    python -m scripts.rename_nivel_valor --campo nivel_1 --de INSTITUCIONAL --a INSTITUCIONALES --apply

Idempotente: si no quedan docs con `--de`, no escribe nada.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

from core.mongo import get_mongo_client

DB = "Clientes"
COL = "Comitentes"
CAMPOS_VALIDOS = {"nivel_1", "nivel_2", "nivel_3", "nivel_4", "nivel_5"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campo", required=True, choices=sorted(CAMPOS_VALIDOS),
                    help="Cuál nivel_X a corregir.")
    ap.add_argument("--de", required=True, help="Valor actual (match exacto).")
    ap.add_argument("--a", required=True, help="Valor nuevo.")
    ap.add_argument("--apply", action="store_true", help="ejecutar (default: dry-run)")
    args = ap.parse_args()

    if args.de == args.a:
        print("--de y --a son iguales. Nada que hacer.")
        return

    col = get_mongo_client()[DB][COL]
    q = {args.campo: args.de}
    n = col.count_documents(q)
    print(f"Docs con {args.campo} == {args.de!r}: {n}")

    # Aviso si ya hay docs con el valor nuevo (para que el user sepa que va a
    # haber convivencia hasta que termine la migración).
    n_dest = col.count_documents({args.campo: args.a})
    if n_dest:
        print(f"Docs ya con {args.campo} == {args.a!r}: {n_dest}  "
              f"(post-migración: {n + n_dest} = {n} renombrados + {n_dest} pre-existentes)")

    if not n:
        print("Nada que cambiar.")
        return

    if not args.apply:
        # Mostrar 10 ejemplos.
        cur = col.find(q, {"_id": 0, "id_cuenta": 1, "denominacion": 1}).limit(10)
        print("\nEjemplos (primeros 10):")
        for d in cur:
            print(f"  {d.get('id_cuenta', '?'):<8s}  {d.get('denominacion', '')}")
        print("\n(dry-run) — pasar --apply para ejecutar.")
        return

    now = datetime.now(UTC)
    r = col.update_many(
        q,
        {"$set": {
            args.campo:        args.a,
            "actualizado_at":  now,
            "actualizado_por": "script:rename_nivel_valor",
        }},
    )
    print(f"\nOK. matched={r.matched_count} modified={r.modified_count}")


if __name__ == "__main__":
    main()
