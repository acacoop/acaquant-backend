"""One-shot: borra de Valuaciones.AuM las posiciones excluidas por las
reglas de negocio definidas en `jobs/_aum_filters.py`.

Después de ejecutar este script, el siguiente paso es:
  1) `python -m scripts.api_migrate aum`         (rebuild PortfolioAPI.AumAPI)
  2) `python -m jobs.aum_resumen_fci --backfill` (rebuild rollup FCI)

Modos:
  --dry-run        Solo imprime qué se borraría (count + sample), no toca data.
  (sin flag)       Borra y reporta `deleted_count`.

Uso:
    python -m scripts.cleanup_aum_excluidos --dry-run
    python -m scripts.cleanup_aum_excluidos
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client
from jobs._aum_filters import load_contrapartes_id_cuentas, mongo_match_excluded


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="No borra nada, solo cuenta y muestra ejemplos.")
    args = ap.parse_args()

    client = get_mongo_client()
    col = client["Valuaciones"]["AuM"]
    contrapartes_ids = load_contrapartes_id_cuentas()
    print(f"contrapartes a excluir (de CuentasAPI.ContrapartesAPI): "
          f"{len(contrapartes_ids)} (matcheo por id_cuenta)")
    match = mongo_match_excluded(contrapartes_ids)

    total = col.count_documents(match)
    print(f"docs que matchean exclusión: {total}")

    if total == 0:
        print("nada que borrar — la colección ya está limpia.")
        return

    sample = list(
        col.find(
            match,
            {"_id": 0, "fecha_snapshot": 1, "cuenta": 1, "unidad": 1, "valuacion": 1},
        ).limit(8)
    )
    print("\nmuestra (8 primeros):")
    for s in sample:
        print(f"  {s.get('fecha_snapshot')}  {s.get('cuenta','')[:60]:60s}  "
              f"unidad={s.get('unidad','')!r:14s}  val={s.get('valuacion')}")

    if args.dry_run:
        print("\n--dry-run: no se borró nada.")
        return

    result = col.delete_many(match)
    print(f"\ndeleted_count: {result.deleted_count}")
    print("listo. Próximos pasos:")
    print("  python -m scripts.api_migrate aum")
    print("  python -m jobs.aum_resumen_fci --backfill")


if __name__ == "__main__":
    main()
