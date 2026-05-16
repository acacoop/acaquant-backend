"""backfill_2026.py — rehace los snapshots de AuM de 2026 (ene, feb, mar).

Mismo flujo que backfill_2025: para cada cierre de mes, EN ORDEN y uno
atrás del otro: DELETE del snapshot -> BACKFILL --solo-aum (desde=fecha+1)
-> FIX de precios --apply.

Marzo va SIN fix de precios (no hay Excel de ese mes) — solo delete +
backfill.

Las cuentas que timeotean quedan en docs/cuentas_con_error.json.

Corre:  python -m scripts.backfill_2026
"""
from __future__ import annotations

import subprocess
import sys

# (fecha_snapshot, JSON de precios o None si el mes no lleva fix).
_MESES: list[tuple[str, str | None]] = [
    ("2026-01-31", "docs/precios_3101.json"),
    ("2026-02-28", "docs/precios_2802.json"),
    ("2026-03-31", None),  # marzo: solo backfill, sin fix de precios
]


def _run(modulo_y_args: list[str]) -> int:
    print(f"\n$ python -m {' '.join(modulo_y_args)}", flush=True)
    return subprocess.run([sys.executable, "-m", *modulo_y_args]).returncode


def main() -> None:
    print(f"backfill_2026 — {len(_MESES)} meses. "
          f"Por mes: DELETE -> BACKFILL -> FIX PRECIOS (marzo sin fix).")
    print("=" * 72)

    ok: list[str] = []
    fallidos: list[str] = []
    for fecha, json_precios in _MESES:
        print(f"\n{'#' * 72}\n# {fecha}\n{'#' * 72}", flush=True)

        if _run(["scripts.delete_snapshot_aum", "--snapshot", fecha, "--apply"]) != 0:
            print(f"⚠ DELETE {fecha} falló — salteo el mes.", flush=True)
            fallidos.append(fecha)
            continue

        if _run(["jobs.aum_backfill", fecha, "--solo-aum"]) != 0:
            print(f"⚠ BACKFILL {fecha} falló — salteo el fix de precios.", flush=True)
            fallidos.append(fecha)
            continue

        if json_precios is None:
            print(f"({fecha}: sin fix de precios — no hay Excel de ese mes)", flush=True)
        elif _run(["scripts.fix_precios_aum", "--precios", json_precios,
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
