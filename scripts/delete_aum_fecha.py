"""Borra todos los docs de `Valuaciones.AuM` para una `fecha_snapshot` puntual.

Pensado para limpiar snapshots dañados antes de re-backfilear o porque
están corruptos / mal cargados.

Privacidad: imprime solo conteos, no docs individuales.

Uso:
    python -m scripts.delete_aum_fecha 2026-01-30 --dry-run
    python -m scripts.delete_aum_fecha 2026-01-30
"""
from __future__ import annotations

import argparse
import sys

from core.mongo import get_mongo_client


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("fecha", help="fecha_snapshot YYYY-MM-DD a borrar")
    ap.add_argument("--dry-run", action="store_true",
                    help="No borra, solo cuenta.")
    args = ap.parse_args()

    if not (len(args.fecha) == 10 and args.fecha[4] == "-" and args.fecha[7] == "-"):
        print(f"Formato inválido: {args.fecha!r}. Usar YYYY-MM-DD.")
        sys.exit(1)

    client = get_mongo_client()
    col = client["Valuaciones"]["AuM"]
    match = {"fecha_snapshot": args.fecha}

    n = col.count_documents(match)
    print(f"fecha_snapshot: {args.fecha}")
    print(f"docs encontrados: {n}")
    if n == 0:
        print("Nada que borrar.")
        return

    # Cuántas cuentas + unidades distintas (para tener idea del alcance).
    n_cuentas = len(col.distinct("id_cuenta", match))
    n_unidades = len(col.distinct("unidad", match))
    print(f"  cuentas distintas:  {n_cuentas}")
    print(f"  unidades distintas: {n_unidades}")

    if args.dry_run:
        print("\n--dry-run: nada se borró.")
        return

    result = col.delete_many(match)
    print(f"\ndeleted_count: {result.deleted_count}")
    print("\nPróximos pasos:")
    print("  python -m scripts.api_migrate aum")
    print("  python -m jobs.aum_resumen_fci --backfill")
    print("  systemctl restart api.service")


if __name__ == "__main__":
    main()
