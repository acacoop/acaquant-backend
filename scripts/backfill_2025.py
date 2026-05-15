"""backfill_2025.py — rehace los snapshots de AuM de 2025 (jul..dic).

Para cada cierre de mes, EN ORDEN y uno atrás del otro:
  1. DELETE del snapshot (borra la foto vieja — la del T+2 mal).
  2. BACKFILL con aum_backfill --solo-aum (pide a Aunesa con desde=fecha+1).
  3. CORRECCIÓN de precios con fix_precios_aum --apply.
Termina un mes y recién ahí pasa al siguiente. El delete de cada mes se
hace justo antes de su backfill — no se borra todo de una.

Junio NO está (ya se corrió a mano).

Las cuentas que timeotean y no se completan ni con reintentos quedan en
docs/cuentas_con_error.json (lo escribe aum_backfill, acumulativo por
snapshot).

Corre:  python -m scripts.backfill_2025
"""
from __future__ import annotations

import subprocess
import sys

# (fecha_snapshot, JSON de precios). Cierres de mes de 2025, jul..dic.
_MESES: list[tuple[str, str]] = [
    ("2025-07-31", "docs/precios_3107.json"),
    ("2025-08-31", "docs/precios_3108.json"),
    ("2025-09-30", "docs/precios_3009.json"),
    ("2025-10-31", "docs/precios_3110.json"),
    ("2025-11-30", "docs/precios_3011.json"),
    ("2025-12-31", "docs/precios_3112.json"),
]


def _run(modulo_y_args: list[str]) -> int:
    print(f"\n$ python -m {' '.join(modulo_y_args)}", flush=True)
    return subprocess.run([sys.executable, "-m", *modulo_y_args]).returncode


def main() -> None:
    print(f"backfill_2025 — {len(_MESES)} meses (jul..dic). "
          f"Por mes: DELETE -> BACKFILL -> FIX PRECIOS.")
    print("=" * 72)

    ok: list[str] = []
    fallidos: list[str] = []
    for fecha, json_precios in _MESES:
        print(f"\n{'#' * 72}\n# {fecha}\n{'#' * 72}", flush=True)

        # 1. DELETE del snapshot viejo.
        if _run(["scripts.delete_snapshot_aum", "--snapshot", fecha, "--apply"]) != 0:
            print(f"⚠ DELETE {fecha} falló — salteo el mes.", flush=True)
            fallidos.append(fecha)
            continue

        # 2. BACKFILL (desde = fecha + 1).
        if _run(["jobs.aum_backfill", fecha, "--solo-aum"]) != 0:
            print(f"⚠ BACKFILL {fecha} falló — salteo el fix de precios.", flush=True)
            fallidos.append(fecha)
            continue

        # 3. CORRECCIÓN de precios.
        if _run(["scripts.fix_precios_aum", "--precios", json_precios,
                 "--snapshot", fecha, "--apply"]) != 0:
            print(f"⚠ FIX PRECIOS {fecha} falló.", flush=True)
            fallidos.append(fecha)
            continue

        print(f"✅ {fecha} listo.", flush=True)
        ok.append(fecha)

    print("\n" + "=" * 72)
    print(f"OK: {ok}")
    if fallidos:
        print(f"⚠ Con error: {fallidos}")
    print("Cuentas que timeotearon → docs/cuentas_con_error.json")


if __name__ == "__main__":
    main()
