"""Normaliza el placeholder de VENCIMIENTO en Valuaciones.Assets.

Dos operaciones:
  (1) GLOBAL: reemplaza VENCIMIENTO == 'NO APLICA' por '-' (cambia el
      placeholder en todos los que ya lo tenían).
  (2) RENTA VARIABLE: setea VENCIMIENTO = '-' masivamente en los assets de
      CARTERA == 'RENTA VARIABLE' que están vacíos ('', 'NO APLICA', null) —
      las acciones no tienen vencimiento. NO pisa fechas reales ya cargadas
      (las reporta para que decidas).

DRY-RUN por defecto (no escribe). Para aplicar:
    python -m scripts.backfill_vencimiento_placeholder                 # dry-run
    python -m scripts.backfill_vencimiento_placeholder --apply         # escribe
    python -m scripts.backfill_vencimiento_placeholder --cartera "RENTA VARIABLE" --apply

Idempotente: re-correr tras --apply no encuentra nada (filtra por placeholder/vacío).
"""
from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime

from core.mongo import get_mongo_client, get_mongo_client_read

_EMPTY_VALUES: list[str | None] = ["", "NO APLICA", None]
_PLACEHOLDER = "-"
_ACTOR = "backfill_vencimiento_placeholder"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Escribe (default: dry-run)")
    ap.add_argument("--cartera", default="RENTA VARIABLE",
                    help="CARTERA a la que sumar '-' en VENCIMIENTO vacío")
    args = ap.parse_args()

    read_db = get_mongo_client_read()
    col = read_db["Valuaciones"]["Assets"]

    print("=== normalizar placeholder VENCIMIENTO en Valuaciones.Assets ===")

    # Confirmar el string exacto de la cartera contra datos reales.
    carteras = [c for c in col.distinct("CARTERA")
                if isinstance(c, str) and re.search(r"renta|variable", c, re.I)]
    print(f"\n[CARTERA candidatas que matchean /renta|variable/i]: {carteras}")
    print(f"  cartera elegida: {args.cartera!r}")

    # (1) NO APLICA → '-' (global).
    n_no_aplica = col.count_documents({"VENCIMIENTO": "NO APLICA"})
    print(f"\n(1) VENCIMIENTO == 'NO APLICA' (global) → '-': {n_no_aplica}")

    # (2) RENTA VARIABLE con VENCIMIENTO vacío → '-'.
    f_rv_vacio = {"CARTERA": args.cartera, "VENCIMIENTO": {"$in": _EMPTY_VALUES}}
    n_rv_vacio = col.count_documents(f_rv_vacio)
    print(f"(2) CARTERA {args.cartera!r} con VENCIMIENTO vacío → '-': {n_rv_vacio}")

    # RV con fecha real (no vacío, no '-') → NO se tocan, solo se reportan.
    f_rv_con_fecha = {
        "CARTERA": args.cartera,
        "VENCIMIENTO": {"$nin": [*_EMPTY_VALUES, _PLACEHOLDER]},
    }
    rv_con_fecha = list(col.find(f_rv_con_fecha, {"_id": 0, "unidad": 1, "VENCIMIENTO": 1}).limit(20))
    n_rv_con_fecha = col.count_documents(f_rv_con_fecha)
    print(f"    (RENTA VARIABLE con fecha real, NO se tocan): {n_rv_con_fecha}")
    for d in rv_con_fecha[:10]:
        print(f"      {d.get('unidad')!r}  VENCIMIENTO={d.get('VENCIMIENTO')!r}")

    if not args.apply:
        print("\nDRY-RUN — no se escribió nada. Re-correr con --apply para aplicar.")
        return

    # --- APPLY ---
    write_col = get_mongo_client()["Valuaciones"]["Assets"]
    now = datetime.now(UTC)
    audit = {"actualizado_por": _ACTOR, "actualizado_at": now}

    r1 = write_col.update_many(
        {"VENCIMIENTO": "NO APLICA"},
        {"$set": {"VENCIMIENTO": _PLACEHOLDER, **audit}},
    )
    # Op2 incluye 'NO APLICA' por si quedó algo, pero ya lo cubrió op1 → idempotente.
    r2 = write_col.update_many(
        {"CARTERA": args.cartera, "VENCIMIENTO": {"$in": _EMPTY_VALUES}},
        {"$set": {"VENCIMIENTO": _PLACEHOLDER, **audit}},
    )

    print("\n✓ APLICADO:")
    print(f"  (1) NO APLICA → '-':                  {r1.modified_count} modificados")
    print(f"  (2) {args.cartera!r} vacío → '-':  {r2.modified_count} modificados")
    print("  Re-sincronizar la copia derivada con: python -m scripts.api_migrate assets")


if __name__ == "__main__":
    main()
