"""retry_cuentas_error.py — reintenta las cuentas que timeotearon en el
backfill jul-oct 2025 y les re-corre el fix de precios.

Las cuentas salieron de `docs/cuentas_con_error.json` (todas ReadTimeout
de Aunesa). Para cada mes, EN ORDEN:

  1. jobs.aum_backfill <fecha> --cuenta <ids> --workers 1 --timeout 360
     --retries 5   (sin paralelismo, timeout largo)
  2. scripts.fix_precios_aum --precios <json> --snapshot <fecha>
     --cuenta <ids> --apply

El fix corre SIEMPRE, aunque el backfill deje alguna cuenta afuera: el
fix solo toca las cuentas que sí quedaron con filas en el snapshot
(las que sigan timeoteando no tienen filas → no se tocan, sin daño).

Pensado para correr con nohup:
  nohup python -m scripts.retry_cuentas_error > retry_cuentas_error.log 2>&1 &
"""
from __future__ import annotations

import subprocess
import sys

# (fecha_snapshot, CSV de id_cuenta, JSON de precios del mes).
_MESES: list[tuple[str, str, str]] = [
    ("2025-07-31", "101,106,108,110,176,194,255", "docs/precios_3107.json"),
    ("2025-08-31", "106,194,255",                 "docs/precios_3108.json"),
    ("2025-09-30", "106",                         "docs/precios_3009.json"),
    ("2025-10-31", "106",                         "docs/precios_3110.json"),
]


def _run(modulo_y_args: list[str]) -> int:
    print(f"\n$ python -m {' '.join(modulo_y_args)}", flush=True)
    return subprocess.run([sys.executable, "-m", *modulo_y_args]).returncode


def main() -> None:
    print(f"retry_cuentas_error — {len(_MESES)} meses. "
          f"Por mes: BACKFILL (--cuenta) -> FIX PRECIOS (--cuenta).")
    print("=" * 72)

    resumen: list[str] = []
    for fecha, ids, json_precios in _MESES:
        print(f"\n{'#' * 72}\n# {fecha} — cuentas {ids}\n{'#' * 72}", flush=True)

        rc_bf = _run([
            "jobs.aum_backfill", fecha, "--cuenta", ids,
            "--workers", "1", "--timeout", "360", "--retries", "5",
        ])
        if rc_bf != 0:
            print(f"⚠ BACKFILL {fecha} terminó con rc={rc_bf} — "
                  f"corro el fix igual (solo toca cuentas con filas).",
                  flush=True)

        rc_fix = _run([
            "scripts.fix_precios_aum", "--precios", json_precios,
            "--snapshot", fecha, "--cuenta", ids, "--apply",
        ])
        if rc_fix != 0:
            print(f"⚠ FIX PRECIOS {fecha} terminó con rc={rc_fix}.", flush=True)

        estado = "✅ OK" if (rc_bf == 0 and rc_fix == 0) else "⚠ revisar"
        resumen.append(f"{fecha}: backfill rc={rc_bf}, fix rc={rc_fix} — {estado}")
        print(f"{estado} — {fecha} listo.", flush=True)

    print("\n" + "=" * 72)
    print("RESUMEN:")
    for linea in resumen:
        print(f"  {linea}")
    print("Cuentas que sigan timeoteando → docs/cuentas_con_error.json "
          "(revisar con scripts.diag_cuentas_con_error).")


if __name__ == "__main__":
    main()
