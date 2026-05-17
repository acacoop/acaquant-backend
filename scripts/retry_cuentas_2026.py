"""retry_cuentas_2026.py — backfillea las cuentas que faltaron en 2026.

`scripts.diag_cuentas_faltantes` detectó cuentas que estaban antes y después
de 2026 pero no en los snapshots de ene/feb/mar — backfill que no las levantó.
Este job, por cada mes, reintenta SOLO esas cuentas:

  1. jobs.aum_backfill <fecha> --cuenta <ids> --workers 1 --timeout 360
     --retries 5
  2. scripts.fix_precios_aum --precios <json> --snapshot <fecha>
     --cuenta <ids> --apply

El fix corre SIEMPRE, aunque el backfill deje alguna afuera (solo toca las
cuentas que sí quedaron con filas). Reintentar una cuenta que igual no tenía
posición es inofensivo — no inserta nada.

Pensado para nohup:
  nohup python -m scripts.retry_cuentas_2026 > retry_cuentas_2026.log 2>&1 &
"""
from __future__ import annotations

import subprocess
import sys

# (fecha_snapshot, CSV de id_cuenta faltantes, JSON de precios del mes).
# Las listas salen de scripts.diag_cuentas_faltantes (2026-05-17).
_MESES: list[tuple[str, str, str]] = [
    (
        "2026-01-31",
        "101,1133,147,153,155,157,164,1646,165,1654,167,169,170,172,180,189,"
        "192,193,194,196,197,209,242,254,26,263,264,288,457,691,760,967",
        "docs/precios_3101.json",
    ),
    (
        "2026-02-28",
        "101,1133,147,153,155,157,164,1646,165,167,169,170,172,180,189,192,"
        "193,194,196,197,242,254,263,264,288,457,691,760,967",
        "docs/precios_2802.json",
    ),
    (
        "2026-03-31",
        "101,1133,147,153,155,157,164,1646,165,167,169,170,172,180,189,192,"
        "193,194,196,197,242,254,263,264,288,457,691,760,967",
        "docs/precios_3103.json",
    ),
]


def _run(modulo_y_args: list[str]) -> int:
    print(f"\n$ python -m {' '.join(modulo_y_args)}", flush=True)
    return subprocess.run([sys.executable, "-m", *modulo_y_args]).returncode


def main() -> None:
    print(f"retry_cuentas_2026 — {len(_MESES)} meses. "
          f"Por mes: BACKFILL (--cuenta) -> FIX PRECIOS (--cuenta).")
    print("=" * 72)

    resumen: list[str] = []
    for fecha, ids, json_precios in _MESES:
        n_ids = len(ids.split(","))
        print(f"\n{'#' * 72}\n# {fecha} — {n_ids} cuentas\n{'#' * 72}", flush=True)

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
    print("Verificar después con scripts.diag_cuentas_faltantes "
          "(las listas deberían quedar vacías o casi).")


if __name__ == "__main__":
    main()
